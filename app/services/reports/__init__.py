"""Report file generation.

Turns a report request (date range, channels, sections, format) into a real
downloadable file — a registry row alone used to be produced with nothing
attached to it. ``content.py`` assembles the data once; one ``render_*`` module
per output format turns that same data into bytes; ``generator.py`` wires the
two together and uploads the result.
"""
