#!/usr/bin/env python
"""Run the vector service."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8901,
        reload=False,
        log_level="info",
    )
