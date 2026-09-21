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
    "Your claim looks likely to be paid **₹1,22,125** once your documents are complete. ₹1,01,625 is confirmed today and ₹20,500 is held until you send the doctor's prescription.",
    "Your plan pays for a room up to ₹5,000 a day and yours cost ₹8,000, so the room and the related doctor and nursing charges are paid at 62.5%.\n\n"
    "- Room rent: ₹12,000 taken off\n- Doctor and other fees: ₹37,875 taken off\n- Non-medical items: ₹12,500 taken off",
    "Your policy expires on 14 Mar 2026.",
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
