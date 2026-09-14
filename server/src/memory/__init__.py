"""Memory system — wraps LangChain Deep Agents Memory with Trajecta-specific interfaces.

Architecture:
  - LangChain Deep Agents Memory handles short-term, semantic, episodic, procedural memory
  - server/src/skills/trajectory_store/ holds raw execution data (separate from memory)
"""