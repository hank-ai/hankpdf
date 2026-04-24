#!/bin/sh
# HankPDF Docker entrypoint.
#
# Wraps hankpdf with a single pre-flight check: if the caller mounted
# something at /data but the image's runtime uid (1000) can't write to
# it, print an actionable hint pointing at --user $(id -u):$(id -g)
# rather than let pikepdf/pdfium crash with a raw EACCES deep in the
# engine.
#
# See docker/README.md §"About -u" for the rationale.
set -e

# Only check /data if it actually exists (i.e. the user mounted something
# there). Without this, running the image without any mount would fail
# here — legitimate for `docker run ghcr.io/hank-ai/hankpdf --version`.
if [ -d /data ] && ! [ -w /data ]; then
    cat >&2 <<EOF
[hankpdf] ERROR: /data is not writable by uid $(id -u).

This usually means you bind-mounted a host directory owned by a uid
other than 1000 (the image's default runtime uid). The container
cannot write its output there.

HINT: re-run with --user "\$(id -u):\$(id -g)" so the container runs
      as your host uid and can write to the bind mount:

    docker run --rm \\
        --user "\$(id -u):\$(id -g)" \\
        -v "\$PWD:/data" \\
        ghcr.io/hank-ai/hankpdf:latest \\
        /data/in.pdf -o /data/out.pdf

See docker/README.md for details and macOS/Windows notes.
EOF
    exit 2
fi

exec hankpdf "$@"
