"""Headless entry point: the engine, a config file, and nothing else.

``ratingrelay.cli`` must stay importable on a box with no web app and no server
running. It may use ``engine/``, ``db/`` and the credential/service construction
in ``core/``; it may not import FastAPI, the routes or the request schemas.
"""
