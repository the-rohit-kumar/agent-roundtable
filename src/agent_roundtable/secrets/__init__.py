"""Secret storage: Windows Credential Manager backend, .env fallback for dev.

Spec (§13.3): secrets live in Windows Credential Manager or an equivalent OS-backed
store; a local .env file may be supported for development only. Values are never
logged, printed, echoed, or committed (§18 rule 5).
"""
