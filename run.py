"""
The actual entry point PyInstaller builds from -- NOT agent/main.py
directly.

agent/main.py (and everything else inside the agent package) uses
relative imports (`from . import config`, `from .api_client import ...`,
etc.), which only resolve correctly when Python has imported "agent" as
a real package. Pointing PyInstaller's Analysis straight at
agent/main.py instead executes that file as the top-level script itself
-- Python then has no parent package context for its relative imports
at all, which is exactly "attempted relative import with no known
parent package".

This tiny file sidesteps that entirely: it imports agent as a proper
package first (so every relative import inside it resolves normally),
then just calls its own main(). Run directly with `python run.py`
during development, or point PyInstaller here (see packaging/agent.spec)
for the packaged build -- both work identically either way.
"""
from agent.main import main

if __name__ == "__main__":
    main()
