import pathlib, tempfile
from app import agent

ws = pathlib.Path(tempfile.mkdtemp())
page = agent.run_turn(ws, "Learning objective: build a URL shortener in Python", resume=False)
print(page)