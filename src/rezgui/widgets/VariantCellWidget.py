from rezgui.qt import QtCore, QtGui
from rezgui.util import create_pane, get_icon_widget, add_menu_action, update_font
from rezgui.models.ContextModel import ContextModel
from rezgui.mixins.ContextViewMixin import ContextViewMixin
from rez.packages import iter_packages
from rez.resolved_context import PatchLock, get_lock_request
from rez.vendor.version.requirement import Requirement, RequirementList
from functools import partial


# TODO deal with variant missing from disk
class VariantCellWidget(QtGui.QWidget, ContextViewMixin):
    def __init__(self, context_model, variant, reference_variant=None,
                 hide_locks=False, read_only=False, parent=None):
        super(VariantCellWidget, self).__init__(parent)
        ContextViewMixin.__init__(self, context_model)

        self.variant = variant
        self.reference_variant = reference_variant
        self.stale = False
        self.lock_status = None
        self.lock_icon = None
        self.hide_locks = hide_locks
        self.read_only = read_only
        self.icons = []  # 3-tuples: widget, name, tooltip

        self.label = QtGui.QLabel(self.variant.qualified_package_name)
        if self.variant.description:
            desc = "%s@%s" % (self.variant.qualified_package_name,
                              self.variant.search_path)
            self.label.setToolTip(desc)

        self.depends_icon = get_icon_widget("depends", "dependent package")
        self.depends_icon.hide()
        create_pane([self.label, self.depends_icon, None],
                    True, compact=True, parent_widget=self)

        self.refresh()

    def text(self):
        return self.variant.qualified_package_name

    def contextMenuEvent(self, event):
        if self.read_only or self.hide_locks:
            return

        current_lock = self.context_model.get_patch_lock(self.variant.name)
        menu = QtGui.QMenu(self)
        consumed_reqs = set()

        for lock_type in PatchLock:
            if lock_type == PatchLock.no_lock:
                desc = lock_type.description
            else:
                req = self._get_lock_requirement(lock_type)
                if lock_type == PatchLock.lock:
                    desc = "Exact version (%s)" % str(req)
                elif req and req not in consumed_reqs:
                    unit = lock_type.description.split()[0]
                    desc = ("%s version updates only (%s.*)"
                            % (unit.capitalize(), str(req)))
                    consumed_reqs.add(req)
                else:
                    continue

            fn = partial(self._set_lock_type, lock_type)
            action = add_menu_action(menu, desc, fn, lock_type.name)
            if lock_type == current_lock:
                action.setEnabled(False)

        menu.addSeparator()
        action = add_menu_action(menu, "Remove Lock", self._remove_lock)
        action.setEnabled(current_lock is not None)

        menu.exec_(self.mapToGlobal(event.pos()))
        menu.setParent(None)

    def refresh(self):
        self._contextChanged(ContextModel.CONTEXT_CHANGED)

    def set_reference_sibling(self, variant=None):
        if variant is None or self.variant.name == variant.name:
            b = False
        else:
            b = self.context_model.package_depends_on(self.variant.name, variant.name)

        update_font(self.label, underline=b)
        self.depends_icon.setVisible(b)

    def _contextChanged(self, flags=0):
        self._set_stale(self.context_model.is_stale())

        if flags & (ContextModel.PACKAGES_PATH_CHANGED |
                    ContextModel.CONTEXT_CHANGED):
            # update icons
            new_icons = []

            if self.variant.index is not None:
                package = self.variant.parent
                if package.num_variants > 1:
                    txt = "1 of %d variants" % package.num_variants
                    new_icons.append(("variant", txt))

            if self.variant.is_local:
                new_icons.append(("local", "package is local"))

            package_paths = self.context_model.packages_path
            if self.variant.search_path in package_paths:
                packages = None
                try:
                    it = iter_packages(name=self.variant.name, paths=package_paths)
                    packages = sorted(it, key=lambda x: x.version)
                except:
                    pass

                # apply a tick icon if appropriate
                ticked = False
                if packages:
                    # test if variant is latest package
                    latest_pkg = packages[-1]
                    if self.variant.version == latest_pkg.version:
                        new_icons.append(("green_tick", "package is latest"))
                        ticked = True

                    range_ = None
                    packages_ = None

                    # test if variant is in request, and is latest possible
                    if not ticked:
                        range_ = None
                        try:
                            request = self.context().requested_packages(True)
                            reqlist = RequirementList(request)
                            if self.variant.name in reqlist.names:
                                variant_ = reqlist.get(self.variant.name)
                                if not variant_.conflict:
                                    range_ = variant_.range
                        except:
                            pass
                        if range_ is not None:
                            packages_ = [x for x in packages if x.version in range_]
                            if packages_:
                                latest_pkg = packages_[-1]
                                if self.variant.version == latest_pkg.version:
                                    new_icons.append(("yellow_tick",
                                                      "package is latest within request"))
                                    ticked = True

                    # test if variant was latest package at time of resolve
                    if not ticked and self.variant.timestamp:
                        untimestamped_packages = [x for x in packages
                                                  if not x.timestamp]
                        if not untimestamped_packages:
                            resolve_time = self.context().timestamp
                            old_packages = [x for x in packages
                                            if x.timestamp <= resolve_time]
                            if old_packages:
                                latest_pkg = old_packages[-1]
                                if self.variant.version == latest_pkg.version:
                                    new_icons.append(
                                        ("green_white_tick",
                                         "package was latest at time of resolve"))
                                    ticked = True

                    # test if variant is in request, and was latest possible at
                    # the time of resolve
                    if (not ticked
                            and self.variant.timestamp
                            and range_ is not None
                            and packages_ is not None):
                        untimestamped_packages = [x for x in packages_
                                                  if not x.timestamp]
                        if not untimestamped_packages:
                            resolve_time = self.context().timestamp
                            old_packages = [x for x in packages_
                                            if x.timestamp <= resolve_time]
                            if old_packages:
                                latest_pkg = old_packages[-1]
                                if self.variant.version == latest_pkg.version:
                                    new_icons.append(
                                        ("yellow_white_tick",
                                         "package was latest within request, "
                                         "at time of resolve"))
                                    ticked = True
            else:
                new_icons.append(("error", "package is not in the search path"))

            self._set_icons(new_icons)

        if (not self.hide_locks) and (flags & (ContextModel.LOCKS_CHANGED |
                                      ContextModel.CONTEXT_CHANGED)):
            # update lock icon
            lock = self.context_model.get_patch_lock(self.variant.name)
            if lock is None:
                lock = self.context_model.default_patch_lock
                icon_name = "%s_faint" % lock.name
            else:
                icon_name = lock.name

            # update lock tooltip
            if lock == PatchLock.no_lock:
                desc = lock.description
            else:
                req = self._get_lock_requirement(lock)
                if req:
                    if lock == PatchLock.lock:
                        desc = "Exact version (%s)" % str(req)
                    else:
                        unit = lock.description.split()[0]
                        desc = ("%s version updates only (%s.*)"
                                % (unit.capitalize(), str(req)))
                else:
                    desc = lock.description

            self._set_lock_icon(icon_name, desc.lower())

    # note: returns the non-weak requirement
    def _get_lock_requirement(self, lock_type):
        if lock_type == PatchLock.no_lock:
            return None
        version = self.reference_variant.version if self.reference_variant \
            else self.variant.version
        return get_lock_request(self.variant.name, version, lock_type, weak=False)

    def _set_lock_type(self, lock_type):
        self.context_model.set_patch_lock(self.variant.name, lock_type)

    def _remove_lock(self):
        self.context_model.remove_patch_lock(self.variant.name)

    def _set_stale(self, b=True):
        if b != self.stale:
            update_font(self.label, italic=b)
            self.stale = b

    def _set_icons(self, icons):
        current_icons = [tuple(x[1:]) for x in self.icons]
        if icons == current_icons:
            return

        layout = self.layout()
        for t in self.icons:
            widget = t[0]
            layout.removeWidget(widget)
            widget.setParent(None)
        self.icons = []

        for name, tooltip in icons:
            widget = get_icon_widget(name, tooltip)
            layout.addWidget(widget)
            self.icons.append((widget, name, tooltip))

    def _set_lock_icon(self, name, tooltip):
        layout = self.layout()
        if self.lock_icon:
            widget_, name_, tooltip_ = self.lock_icon
            if name == name_:
                return
            layout.removeWidget(widget_)
            widget_.setParent(None)

        widget = get_icon_widget(name, tooltip)
        layout.insertWidget(0, widget)
        self.lock_icon = (widget, name, tooltip)
