"""Tests never talk to Azure, whatever .env says (bootstrap_azure.py --enable-azure sets RETRIEVER=azure and AGENT_MODE=foundry there).

Real environment variables win over .env (python-dotenv does not override), so setting them before `app` is imported is enough.
"""
import os

os.environ["RETRIEVER"] = "local"
os.environ["AGENT_MODE"] = "offline"
