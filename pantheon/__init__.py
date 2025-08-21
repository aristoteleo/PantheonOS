__version__ = "0.3.2"

# Support namespace packages for internal toolset modules
__path__ = __import__('pkgutil').extend_path(__path__, __name__)
