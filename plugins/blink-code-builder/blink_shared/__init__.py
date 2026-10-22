"""Code shared by the plugin's hooks (hooks/). Stdlib only: the hooks run with the system
python3 and install nothing.

The hooks put the plugin root on sys.path in hooks/_common.py, which is what makes
`import blink_shared.config` resolve.
"""
