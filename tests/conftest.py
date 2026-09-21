"""Tests never talk to Azure, whatever .env says (bootstrap_azure.py --enable-azure sets RETRIEVER=azure and AGENT_MODE=foundry there).

Real environment variables win over .env (python-dotenv does not override), so setting them before `app` is imported is enough.
"""
import os

os.environ["RETRIEVER"] = "local"
os.environ["AGENT_MODE"] = "offline"
os.environ["CORS_ORIGINS"] = ""
os.environ["ARC_TODAY"] = "2025-01-01"            # the engine's "today" for the tests: before every sample discharge, so no case is a late filing unless a test says so
os.environ["DEBUG"] = "1"                    # the developer routes are on for the tests that use them; test_surface.py switches them off
os.environ["RATE_LIMIT_PER_MIN"] = "100000"
os.environ["CHAT_RATE_LIMIT_PER_MIN"] = "100000"
