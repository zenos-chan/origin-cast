import json
import inspect
import importlib
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Any
from extensions.base import BaseExtension
from extensions.hdhub4u import HDHub4uExtension


class ExtensionManager:
    """
    Manages loading, registering, enabling, and running site-specific scraper extensions.
    """
    def __init__(self, extensions_dir: Optional[str] = None, registry_file: Optional[str] = None):
        self.extensions_dir = Path(extensions_dir or Path(__file__).parent).resolve()
        self.registry_file = Path(registry_file or (self.extensions_dir / "registry.json")).resolve()
        self.extensions: Dict[str, BaseExtension] = {}
        self.registry_data: Dict[str, Any] = {}
        self._load_registry()
        self.discover_extensions()

    def _load_registry(self):
        """Loads persistent extension states (enabled/disabled, custom configs)."""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, "r", encoding="utf-8") as f:
                    self.registry_data = json.load(f)
            except Exception:
                self.registry_data = {}
        else:
            self.registry_data = {}

    def _save_registry(self):
        """Persists extension states to registry.json."""
        try:
            with open(self.registry_file, "w", encoding="utf-8") as f:
                json.dump(self.registry_data, f, indent=2)
        except Exception as e:
            print(f"[!] Warning: Could not save extensions registry: {e}")

    def register_extension(self, extension_instance: BaseExtension):
        """Registers an extension instance into memory, applying persisted states."""
        ext_id = extension_instance.id
        # Restore persisted enabled state if exists
        if ext_id in self.registry_data:
            persisted = self.registry_data[ext_id]
            extension_instance.enabled = persisted.get("enabled", True)
            if "base_url" in persisted:
                extension_instance.base_url = persisted["base_url"]
        else:
            self.registry_data[ext_id] = {
                "id": ext_id,
                "name": extension_instance.name,
                "enabled": extension_instance.enabled,
                "base_url": extension_instance.base_url
            }
            self._save_registry()

        self.extensions[ext_id] = extension_instance

    def discover_extensions(self):
        """
        Discovers and dynamically instantiates all extensions in the extensions folder.
        Always includes HDHub4u by default.
        """
        # Register standard built-in extensions
        hdhub4u_inst = HDHub4uExtension()
        self.register_extension(hdhub4u_inst)

        # Scan folder for any extra user-added .py scraper files
        for py_file in self.extensions_dir.glob("*.py"):
            stem = py_file.stem
            if stem in ("__init__", "base", "manager", "hdhub4u"):
                continue

            try:
                spec = importlib.util.spec_from_file_location(f"extensions.{stem}", str(py_file))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    for _, cls in inspect.getmembers(mod, inspect.isclass):
                        if issubclass(cls, BaseExtension) and cls is not BaseExtension:
                            instance = cls()
                            self.register_extension(instance)
            except Exception as e:
                print(f"[!] Failed to load dynamic extension from {py_file}: {e}")

    def list_extensions(self) -> List[Dict[str, Any]]:
        """Returns list of all registered extensions with metadata."""
        return [ext.to_dict() for ext in self.extensions.values()]

    def get_extension(self, extension_id: str) -> Optional[BaseExtension]:
        """Returns the extension instance by ID."""
        return self.extensions.get(extension_id)

    def get_latest(self, extension_id: str, page: int = 1) -> List[Dict[str, Any]]:
        """Returns latest movies feed from the specified extension."""
        ext = self.get_extension(extension_id)
        if ext and hasattr(ext, "get_latest"):
            return ext.get_latest(page=page)
        return []

    def toggle_extension(self, extension_id: str, enabled: Optional[bool] = None) -> bool:
        """Toggles or sets the enabled status of an extension."""
        ext = self.get_extension(extension_id)
        if not ext:
            raise KeyError(f"Extension '{extension_id}' not found.")

        if enabled is None:
            ext.enabled = not ext.enabled
        else:
            ext.enabled = bool(enabled)

        if extension_id not in self.registry_data:
            self.registry_data[extension_id] = {}
        self.registry_data[extension_id]["enabled"] = ext.enabled
        self._save_registry()
        return ext.enabled

    def update_extension_url(self, extension_id: str, new_url: str) -> str:
        """Updates the target base URL of an extension and persists it."""
        ext = self.get_extension(extension_id)
        if not ext:
            raise KeyError(f"Extension '{extension_id}' not found.")

        clean_url = new_url.strip().rstrip('/')
        ext.base_url = clean_url

        if extension_id not in self.registry_data:
            self.registry_data[extension_id] = {}
        self.registry_data[extension_id]["base_url"] = clean_url
        self._save_registry()
        return clean_url

    def create_custom_extension(
        self,
        ext_id: str,
        name: str,
        base_url: str,
        description: str,
        code: Optional[str] = None
    ) -> BaseExtension:
        """
        Creates a new scraper extension file in extensions/ and registers it dynamically.
        """
        clean_id = "".join(c for c in ext_id.lower() if c.isalnum() or c == "_")
        if not clean_id:
            clean_id = f"scraper_{len(self.extensions) + 1}"

        target_file = self.extensions_dir / f"{clean_id}.py"

        # Generate default template code if none provided
        if not code or not code.strip():
            class_name = f"{''.join(w.capitalize() for w in clean_id.split('_'))}Extension"
            code = f'''import urllib.parse
from typing import Dict, List, Any
from extensions.base import BaseExtension

class {class_name}(BaseExtension):
    id = "{clean_id}"
    name = "{name}"
    description = "{description}"
    base_url = "{base_url.rstrip('/')}"
    version = "1.0.0"
    author = "Admin User"
    icon = "globe"
    enabled = True

    def search(self, query: str) -> List[Dict[str, Any]]:
        # Target search logic
        return [
            {{
                "title": f"{{query.title()}} (2024) Multi Quality",
                "url": f"{{self.base_url}}/movie/{{query.lower().replace(' ', '-')}}",
                "poster": "https://images.unsplash.com/photo-1514565131-fce0801e5785?auto=format&fit=crop&w=600&q=80",
                "extension": self.id
            }}
        ]

    def scrape(self, url: str) -> Dict[str, Any]:
        slug = url.rstrip("/").split("/")[-1]
        title = slug.replace("-", " ").title()
        return {{
            "id": f"{clean_id}-{{abs(hash(url)) % 1000000}}",
            "title": title,
            "poster": "https://images.unsplash.com/photo-1514565131-fce0801e5785?auto=format&fit=crop&w=600&q=80",
            "overview": f"Movie scraped from {name} at {{url}}.",
            "qualities": [
                {{
                    "quality": "720p",
                    "size": "950 MB",
                    "links": [
                        {{"label": "Direct Fast Link", "url": f"{{url}}/download-720p", "type": "Direct"}},
                        {{"label": "Cloud Mirror", "url": f"https://hubcloud.club/drive/{{slug}}", "type": "HubCloud"}}
                    ]
                }},
                {{
                    "quality": "1080p",
                    "size": "2.2 GB",
                    "links": [
                        {{"label": "Full HD Direct", "url": f"{{url}}/download-1080p", "type": "Direct"}},
                        {{"label": "G-Drive Mirror", "url": f"https://drive.google.com/file/d/{{slug}}", "type": "Google Drive"}}
                    ]
                }}
            ],
            "source_url": url,
            "extension": self.id
        }}
'''

        with open(target_file, "w", encoding="utf-8") as f:
            f.write(code)

        # Load dynamic extension
        spec = importlib.util.spec_from_file_location(f"extensions.{clean_id}", str(target_file))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for _, cls in inspect.getmembers(mod, inspect.isclass):
                if issubclass(cls, BaseExtension) and cls is not BaseExtension:
                    instance = cls()
                    self.register_extension(instance)
                    return instance

        raise RuntimeError(f"Could not load newly created extension class from {target_file}")

    def delete_custom_extension(self, extension_id: str) -> bool:
        """Deletes a custom extension file."""
        if extension_id == "hdhub4u":
            raise ValueError("Built-in extension HDHub4u cannot be deleted.")

        if extension_id in self.extensions:
            del self.extensions[extension_id]
        if extension_id in self.registry_data:
            del self.registry_data[extension_id]
            self._save_registry()

        target_file = self.extensions_dir / f"{extension_id}.py"
        if target_file.exists():
            target_file.unlink()
            return True
        return False


# Global default extension manager instance
default_extension_manager = ExtensionManager()
