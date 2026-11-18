"""WSGI entry point for production servers such as Waitress."""

from app import app

__all__ = ["app"]
