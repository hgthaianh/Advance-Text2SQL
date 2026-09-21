"""Process-wide test configuration before application modules are imported."""

import os


os.environ.setdefault("AUTH_SECRET_KEY", "test-only-auth-secret-key-at-least-32-characters")
