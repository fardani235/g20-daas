"""Analysis plugin runtime package.

- ``geospatial``: HTTP client for the geospatial analysis API (System plugins)
- ``sync``: catalog synchronization into ``WebODM Plugin`` rows (System plugins)
- ``package``: validation of uploaded user plugin packages
- ``sandbox``: staging + HTTP client for the plugin runner (User plugins)
- ``runner``: RQ job that executes a plugin run, dispatching on plugin type
- ``files``: File <-> on-disk path helpers shared by all of the above
"""
