# Use the OS trust store (when truststore is installed) before any module imports pymongo or httpx:
# injecting after pymongo is imported recurses. TLS verification stays on.
from .atlas_startup import use_system_trust_store as _use_system_trust_store

_use_system_trust_store()
