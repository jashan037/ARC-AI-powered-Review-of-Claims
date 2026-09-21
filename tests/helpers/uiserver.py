"""A real server on a free port for the browser tests and the screenshots: the app itself, a stand-in agent with fixed plain replies (no Azure, no model)."""
from __future__ import annotations

import socket
import threading
import time
import urllib.request

import uvicorn

from app import main
from app.agent.runner import AgentResult

REPLIES = [
    "About **₹1,22,125** of your ₹1,84,500 bill looks payable. ₹1,01,625 is counted so far, and ₹20,500 is waiting for the doctor's prescription for your pharmacy bills.",
    "Your claim looks likely to be paid, at about **₹1,22,125** of the ₹1,84,500 bill once one document arrives.\n\n"
    "| | Amount |\n|---|---:|\n| Hospital bill | ₹1,84,500 |\n| Room cost above your plan's limit | −₹49,875 |\n| Extras your plan doesn't cover | −₹12,500 |\n| **Estimated payment** | **₹1,22,125** |\n\n"
    "Your room was ₹8,000 a day against your plan's ₹5,000, so the room and related charges are reduced in the same proportion.",
    "Your policy expires on 14 Mar 2027.",
    "I don't see that in your documents.",
]


class StandInAgent:
    def __init__(self):
        self.n = 0

    def ask(self, session, message):
        self.n += 1
        return AgentResult(REPLIES[(self.n - 1) % len(REPLIES)], [], [], "ok", 5)


class Server:
    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.agent = StandInAgent()
        main.get_agent = lambda: self.agent
        self.server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url + "/nope", timeout=1)
            except urllib.error.HTTPError:
                return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("the test server did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)
