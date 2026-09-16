Place the approved Linux wheelhouse here before offline build or CI. Include ALL transitive
dependencies from requirements-dev.txt for the same Python version and architecture as
PYTHON_IMAGE. Obtain packages from the corporate mirror in a trusted preparation environment.
Do not copy a macOS wheelhouse into a Linux image. This source archive contains no binary wheels.
For repeatable releases record wheel SHA256 checksums and pin the internal base image digest.
