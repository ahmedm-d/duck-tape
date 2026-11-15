# Labelled dependency-remediation fixture

This is a small local test repository, not a production service or exploit.
Its captured CVE-2024-35195 advisory affects requests versions below 2.32.0.
The existing test constructs a Session without making network requests.

A fixed universal-wheel candidate for this advisory is:

```text
requests==2.32.3 --hash=sha256:70761cfe03c773ceb22aa2f671b4757976145175cdfca038c02654d061d6dcc6
```

The SHA256 was read from public PyPI release metadata. Keep other exact pins
and existing tests. The generic worker must execute checks and re-query the
real advisory before claiming this particular advisory was remediated.
This does not claim requests 2.32.3 or the other historical pins are free of
other advisories; this demo evaluates one explicitly bound advisory only.
