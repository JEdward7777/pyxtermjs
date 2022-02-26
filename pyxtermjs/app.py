#!/usr/bin/env python3
import argparse
from flask import Flask, render_template, request
from flask_socketio import SocketIO
import pty
import os
import subprocess
import select
import termios
import struct
import fcntl
import shlex
import logging
import sys
import signal

logging.getLogger("werkzeug").setLevel(logging.ERROR)

__version__ = "0.5.0.0"

app = Flask(__name__, template_folder=".", static_folder=".", static_url_path="")
app.config["SECRET_KEY"] = "secret!"
app.config["sid2fd"] = {}
app.config["fd2sid"] = {}
app.config["sid2pid"] = {}
socketio = SocketIO(app)


def set_winsize(fd, row, col, xpix=0, ypix=0):
    logging.debug("setting window size with termios")
    winsize = struct.pack("HHHH", row, col, xpix, ypix)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def read_and_forward_pty_output():
    max_read_bytes = 1024 * 20
    while True:
        socketio.sleep(0.01)
        if app.config["fd2sid"]:
            timeout_sec = 1
            (data_ready, _, _) = select.select(app.config["fd2sid"].keys(), [], [], timeout_sec)
            for fd_ready in data_ready:
                try:
                    #make sure it didn't get removed.
                    if fd_ready in app.config["fd2sid"]:
                        output = os.read(fd_ready, max_read_bytes).decode()
                        socketio.emit("pty-output", {"output": output}, namespace="/pty", room=app.config["fd2sid"][fd_ready] )
                except OSError:
                    if fd_ready in app.config["fd2sid"]:
                        sid = app.config["fd2sid"][fd_ready]
                        if sid in app.config["sid2fd"]:
                            del app.config["sid2fd"][sid]
                        if sid in app.config["sid2pid"]:
                            pid = app.config["sid2pid"][sid]
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except:
                                pass
                            del app.config["sid2pid"][sid]
                        del app.config["fd2sid"][fd_ready]
                            



socketio.start_background_task(target=read_and_forward_pty_output)

@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("pty-input", namespace="/pty")
def pty_input(data):
    """write to the child pty. The pty sees this as if you are typing in a real
    terminal.
    """
    if request.sid in app.config["sid2fd"]:
        logging.debug("received input from browser: %s" % data["input"])
        os.write(app.config["sid2fd"][request.sid], data["input"].encode())


@socketio.on("resize", namespace="/pty")
def resize(data):
    if request.sid in app.config["sid2fd"]:
        logging.debug(f"Resizing window to {data['rows']}x{data['cols']}")
        set_winsize(app.config["sid2fd"][request.sid], data["rows"], data["cols"])

@socketio.on("disconnect", namespace="/pty")
def disconnect():
    """client disconnected"""
    logging.info("client disconnected")

    if request.sid in app.config["sid2fd"]:
        fd = app.config["sid2fd"][request.sid]
        os.write( fd, "\x03".encode() ) #send a control-c
        if fd in app.config["fd2sid"]:
            del app.config["fd2sid"][fd]
        del app.config["sid2fd"][request.sid]

    if request.sid in app.config["sid2pid"]:
        pid = app.config["sid2pid"][request.sid]
        os.kill(pid, signal.SIGKILL)

@socketio.on("connect", namespace="/pty")
def connect():
    """new client connected"""
    logging.info("new client connected")
    if request.sid in app.config["sid2fd"]:
        # already started child process, don't start another
        return

    # create child process attached to a pty we can read from and write to
    (child_pid, fd) = pty.fork()
    if child_pid == 0:
        # this is the child process fork.
        # anything printed here will show up in the pty, including the output
        # of this subprocess
        subprocess.run(app.config["cmd"])
    else:
        # this is the parent process fork.
        # store child fd and pid
        app.config["fd2sid"][fd] = request.sid
        app.config["sid2fd"][request.sid] = fd
        app.config["sid2pid"][request.sid] = child_pid
        #app.config["child_pid"] = child_pid
        set_winsize(fd, 50, 50)
        cmd = " ".join(shlex.quote(c) for c in app.config["cmd"])

        logging.info("child pid is " + child_pid)
        logging.info(
            f"starting background task with command `{cmd}` to continously read "
            "and forward pty output to client"
        )
        logging.info("task started")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "A fully functional terminal in your browser. "
            "https://github.com/cs01/pyxterm.js"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-p", "--port", default=5000, help="port to run server on")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host to run server on (use 0.0.0.0 to allow access from other hosts)",
    )
    parser.add_argument("--debug", action="store_true", help="debug the server")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument(
        "--command", default="bash", help="Command to run in the terminal"
    )
    parser.add_argument(
        "--cmd-args",
        default="",
        help="arguments to pass to command (i.e. --cmd-args='arg1 arg2 --flag')",
    )
    args = parser.parse_args()
    if args.version:
        print(__version__)
        exit(0)
    app.config["cmd"] = [args.command] + shlex.split(args.cmd_args)
    green = "\033[92m"
    end = "\033[0m"
    log_format = green + "pyxtermjs > " + end + "%(levelname)s (%(funcName)s:%(lineno)s) %(message)s"
    logging.basicConfig(
        format=log_format,
        stream=sys.stdout,
        level=logging.DEBUG if args.debug else logging.INFO,
    )
    logging.info(f"serving on http://{args.host}:{args.port}")
    socketio.run(app, debug=args.debug, port=args.port, host=args.host)


if __name__ == "__main__":
    main()
