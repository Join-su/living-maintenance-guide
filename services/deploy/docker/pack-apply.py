#!/usr/bin/env python3
"""Pack apply tool as the watcher calls it (AD_PACK_TOOL), with the deploy-request schema from the image.

The real tool is <Pack repository>/tools/change/receive_approved.py. Without --schema it looks for the schema in a folder next
to the Pack repository; here it gets the AgentOps copy of the same file (llmops/schemas/approved-change@2.schema.json) instead.
Every argument the watcher gives (--repo · --branch · --packrt-accept · --apply · --by ...) goes through unchanged.
"""
import os
import sys

SCHEMA = "/opt/llmops-gate/llmops/schemas/approved-change@2.schema.json"
args = sys.argv[1:]
repo = args[args.index("--repo") + 1]
tool = os.path.join(repo, "tools", "change", "receive_approved.py")
os.execv(sys.executable, [sys.executable, "-B", tool, *args, "--schema", SCHEMA])
