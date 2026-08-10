"""Session-wide test setup.

Points TAGGER_LOG_DIR at a throwaway temp directory *before* anything
imports tagger.main (whose module-level configure_logging() call would
otherwise bind to the real project's logs/app.log on first import,
mirroring the TAGGER_DATA_DIR pattern used for per-test config/db
isolation).
"""

import os
import tempfile

os.environ.setdefault("TAGGER_LOG_DIR", tempfile.mkdtemp(prefix="tagger-test-logs-"))
