"""Populate small token vocabularies once so subsequent checks work offline."""
import truststore
truststore.inject_into_ssl()
import tiktoken

for name in ("o200k_base", "cl100k_base"):
    tiktoken.get_encoding(name)
    print("Token vocabulary ready:", name)
