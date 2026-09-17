Place the approved Linux wheelhouse here before offline build or CI. Include ALL transitive
dependencies from requirements-dev.txt for the same Python version and architecture as
PYTHON_IMAGE. Obtain packages from the corporate mirror in a trusted preparation environment.
Do not copy a macOS wheelhouse into a Linux image. This source archive contains no binary wheels.
For repeatable releases record wheel SHA256 checksums and pin the internal base image digest.

For a server installation without Docker, production dependencies are enough. Prepare them on a
machine with the same operating system, architecture and Python 3.12:

    python3.12 -m pip download --only-binary=:all: -r requirements.txt -d wheels \
      --index-url https://<internal-pypi>/simple

Copy the complete project archive with `wheels/*.whl` into the closed contour, then run:

    python3 install.py --offline
    python3 run.py

The project uses plain Uvicorn without optional `standard` extras, so the wheelhouse does not need
uvloop, httptools, watchfiles or websockets. It still must contain every transitive dependency selected
by pip for `requirements.txt`.
