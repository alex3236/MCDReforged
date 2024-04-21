import contextlib
import os
import sys
from abc import ABC
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Optional, List, Tuple, Any

from typing_extensions import override

from mcdreforged.plugin.meta.metadata import Metadata
from mcdreforged.plugin.plugin_event import MCDRPluginEvents, EventListener, PluginEvent
from mcdreforged.plugin.plugin_registry import DEFAULT_LISTENER_PRIORITY
from mcdreforged.plugin.type.plugin import AbstractPlugin, PluginState
from mcdreforged.utils import time_util
from mcdreforged.utils.exception import IllegalCallError
from mcdreforged.utils.logger import DebugOption

if TYPE_CHECKING:
	from mcdreforged.plugin.plugin_manager import PluginManager

MODULE_TYPE = Any


class RegularPlugin(AbstractPlugin, ABC):
	"""
	User-provided plugin with associated file
	"""
	def __init__(self, plugin_manager: 'PluginManager', file_path: Path):
		super().__init__(plugin_manager)
		self.file_path: Path = file_path
		self.file_name: str = file_path.name
		self.file_modify_time: Optional[int] = None
		self.__metadata: Optional[Metadata] = None
		self.entry_module_instance: MODULE_TYPE = None
		self.old_entry_module_instance: MODULE_TYPE = None
		self.decorated_event_listeners: List[Tuple[PluginEvent, EventListener]] = []

	@property
	def plugin_path(self) -> Path:
		# XXX: legacy usage, yeet
		return self.file_path

	def _reset(self):
		self.file_modify_time = self.calculate_file_modify_time()
		self.plugin_registry.clear()

	@override
	def get_metadata(self) -> Metadata:
		if self.__metadata is None:
			raise IllegalCallError('Meta data of plugin {} is not loaded. Plugin state = {}'.format(repr(self), self.state))
		return self.__metadata

	def _set_metadata(self, metadata: Metadata):
		self.__metadata = metadata

	@property
	def __class_name(self):
		return self.__class__.__name__

	def __repr__(self):
		return '{}[file={},path={},state={}]'.format(self.__class_name, self.file_name, self.plugin_path, self.state)

	# ----------------------
	#   Instance Operation
	# ----------------------

	def is_own_module(self, module_name: str) -> bool:
		raise NotImplementedError()

	def _load_entry_instance(self):
		self.old_entry_module_instance = self.entry_module_instance
		with self.plugin_manager.with_plugin_context(self):
			self.entry_module_instance = self._import_entrypoint_module()

	# ---------------------
	#   To be Implemented
	# ---------------------

	def _import_entrypoint_module(self) -> ModuleType:
		raise NotImplementedError()

	def _on_ready(self):
		self._register_default_listeners()

	def _on_load(self):
		self._reset()

	def _on_unload(self):
		for module_name in sys.modules.copy().keys():
			if self.is_own_module(module_name):
				rv = sys.modules.pop(module_name, None)
				self.mcdr_server.logger.debug('Removed module {} when unloading plugin {}, success = {}'.format(module_name, repr(self), rv is not None), option=DebugOption.PLUGIN)

	# --------------
	#   Life Cycle
	# --------------

	def __do_load(self):
		self.set_state(PluginState.LOADING)
		self._on_load()
		self.set_state(PluginState.LOADED)

	@override
	def load(self):
		self.assert_state({PluginState.UNINITIALIZED})
		self.__do_load()
		self.mcdr_server.logger.debug('{} {} loaded from {}, file modify time = {}'.format(self.__class_name, self, self.plugin_path, self.pretty_file_modify_time), option=DebugOption.PLUGIN)

	@override
	def ready(self):
		"""
		Get ready, and register default things (listeners etc.)
		"""
		self.assert_state({PluginState.LOADED})
		self._on_ready()
		self.set_state(PluginState.READY)

	@override
	def reload(self):
		self.assert_state({PluginState.UNLOADING})
		self.__do_load()
		self.mcdr_server.logger.debug('{} {} reloaded, file modify time = {}'.format(self.__class_name, self, self.pretty_file_modify_time))

	@override
	def unload(self):
		self.assert_state({PluginState.LOADING, PluginState.LOADED, PluginState.READY})
		self._on_unload()
		self.set_state(PluginState.UNLOADING)

	@override
	def remove(self):
		self.assert_state({PluginState.UNLOADING})
		self.set_state(PluginState.UNLOADED)

	# -------------------
	#   Plugin Registry
	# -------------------

	def _register_default_listeners(self):
		for event in MCDRPluginEvents.get_event_list():
			if isinstance(event.default_method_name, str):
				func = getattr(self.entry_module_instance, event.default_method_name, None)
				if callable(func):
					self.register_event_listener(event, EventListener(self, func, DEFAULT_LISTENER_PRIORITY))
		for event, listener in self.decorated_event_listeners:
			self.register_event_listener(event, listener)
		self.decorated_event_listeners.clear()

	@override
	def register_event_listener(self, event: PluginEvent, listener: EventListener):
		# Special handling event listener registered with @event_listener decorator
		# Store and register them in method _register_default_listeners
		if self.in_states({PluginState.LOADING}):
			self.decorated_event_listeners.append((event, listener))
		else:
			super().register_event_listener(event, listener)

	# ---------------
	#   Plugin File
	# ---------------

	def plugin_exists(self) -> bool:
		return self.plugin_path.is_file()

	def file_changed(self) -> bool:
		return self.calculate_file_modify_time() != self.file_modify_time

	@property
	def pretty_file_modify_time(self) -> Optional[str]:
		if self.file_modify_time is not None:
			return time_util.format_time('%Y-%m-%d %H:%M:%S', self.file_modify_time / 1e9) or str(self.file_modify_time)
		else:
			return None

	def calculate_file_modify_time(self) -> Optional[int]:
		if self.plugin_exists():
			with contextlib.suppress(OSError):
				return os.stat(self.plugin_path).st_mtime_ns
		return None
