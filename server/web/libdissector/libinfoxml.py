# vim: set ts=4 sw=4 et: coding=UTF-8

#
# Copyright (c) 2009-2010, Novell, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#  * Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#  * Neither the name of the <ORGANIZATION> nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
#
# (Licensed under the simplified BSD license)
#
# Authors: Vincent Untz <vuntz@opensuse.org>
#

import os
import sys

import sqlite3
import tempfile

try:
    from lxml import etree as ET
except ImportError:
    try:
        from xml.etree import cElementTree as ET
    except ImportError:
        import cElementTree as ET

import config
import libdbcore

# Directory containing XML caches for projects data
XML_CACHE_DIR = os.path.join(config.datadir, 'xml')


#######################################################################


class InfoXmlException(Exception):

    def __init__(self, value):
        self.msg = value

    def __str__(self):
        return self.msg


#######################################################################


class InfoXml:

    version_query = 'SELECT %s.version FROM %s, %s WHERE %s.name = ? AND %s.name = ? AND %s.project = %s.id ;' % (libdbcore.table_srcpackage, libdbcore.table_project, libdbcore.table_srcpackage, libdbcore.table_project, libdbcore.table_srcpackage, libdbcore.table_srcpackage, libdbcore.table_project)

    def __init__(self, obsdb = None):
        if not obsdb:
            self.obsdb = libdbcore.ObsDb()
        else:
            if not isinstance(obsdb, libdbcore.ObsDb):
                raise TypeError, 'obsdb must be a ObsDb instance'
            self.obsdb = obsdb

        self.cursor = self.obsdb.cursor_new()
        self.cursor_helper = self.obsdb.cursor_new()

        self.cache_dir = XML_CACHE_DIR
        self.version_cache = None

    def _find_version_for_sql(self, project, package):
        self.cursor_helper.execute(self.version_query, (project, package))
        row = self.cursor_helper.fetchone()
        if row:
            return row['version']
        else:
            return None

    def _find_version_for(self, project, package):
        # We have a cache here because we want to avoid doing SQL queries.
        # See also comment in create_cache()
        if self.version_cache is None:
            return self._find_version_for_sql(project, package)

        try:
            return self.version_cache[project][package]
        except KeyError:
            return None

    def _get_package_node_from_row(self, row, ignore_upstream, default_parent_project):
        name = row['name']
        version = row['version']
        link_project = row['link_project']
        link_package = row['link_package']
        devel_project = row['devel_project']
        devel_package = row['devel_package']
        upstream_version = row['upstream_version']
        upstream_url = row['upstream_url']
        is_link = row['is_obs_link']
        has_delta = row['obs_link_has_delta']
        error = row['obs_error']
        error_details = row['obs_error_details']

        parent_version = None
        devel_version = None

        package = ET.Element('package')
        package.set('name', name)

        if link_project:
            if (link_project != default_parent_project) or (link_package and link_package != name):
                node = ET.SubElement(package, 'parent')
                node.set('project', link_project)
                if link_package and link_package != name:
                    node.set('package', link_package)
            parent_version = self._find_version_for(link_project, link_package or name)
        elif default_parent_project:
            parent_version = self._find_version_for(default_parent_project, name)

        if devel_project:
            node = ET.SubElement(package, 'devel')
            node.set('project', devel_project)
            if devel_package and devel_package != name:
                node.set('package', devel_package)
            devel_version = self._find_version_for(devel_project, devel_package or name)

        if version or upstream_version or parent_version or devel_version:
            node = ET.SubElement(package, 'version')
            if version:
                node.set('current', version)
            if upstream_version:
                node.set('upstream', upstream_version)
            if parent_version:
                node.set('parent', parent_version)
            if devel_version:
                node.set('devel', devel_version)

        if upstream_url:
            upstream = ET.SubElement(package, 'upstream')
            if upstream_url:
                node = ET.SubElement(upstream, 'url')
                node.text = upstream_url

        if is_link:
            node = ET.SubElement(package, 'link')
            if has_delta:
                node.set('delta', 'true')
            else:
                node.set('delta', 'false')
        # deep delta (ie, delta in non-link packages)
        elif has_delta:
            node = ET.SubElement(package, 'delta')

        if error:
            node = ET.SubElement(package, 'error')
            node.set('type', error)
            if error_details:
                node.text = error_details

        return package

    def get_package_node(self, project, package):
        self.cursor.execute(libdbcore.pkg_query, (project, package))
        row = self.cursor.fetchone()
        
        if not row:
            raise InfoXmlException('Non existing package in project %s: %s' % (project, package))

        self.cursor_helper.execute('''SELECT * FROM %s WHERE name = ?;''' % libdbcore.table_project, (project,))

        row_helper = self.cursor_helper.fetchone()
        parent_project = row_helper['parent']
        ignore_upstream = row_helper['ignore_upstream']

        return self._get_package_node_from_row(row, ignore_upstream, parent_project)

    def get_project_node(self, project, filled = True, write_cache = False):
        if filled:
            prj_node = self._read_cache(project)
            if prj_node is not None:
                return prj_node

        self.cursor.execute('''SELECT * FROM %s WHERE name = ?;''' % libdbcore.table_project, (project,))
        row = self.cursor.fetchone()

        if not row:
            raise InfoXmlException('Non existing project: %s' % project)

        project_id = row['id']
        parent_project = row['parent']
        ignore_upstream = row['ignore_upstream']

        prj_node = ET.Element('project')
        prj_node.set('name', project)
        if parent_project:
            prj_node.set('parent', parent_project)
        if ignore_upstream:
            prj_node.set('ignore_upstream', 'true')

        if not filled:
            return prj_node

        should_exist = {}
        self.cursor.execute('''SELECT A.name AS parent_project, B.name AS parent_package, B.devel_package FROM %s AS A, %s AS B WHERE A.id = B.project AND devel_project = ? ORDER BY A.name, B.name;''' % (libdbcore.table_project, libdbcore.table_srcpackage), (project,))
        for row in self.cursor:
            should_parent_project = row['parent_project']
            should_parent_package = row['parent_package']
            should_devel_package = row['devel_package'] or should_parent_package
            should_exist[should_devel_package] = (should_parent_project, should_parent_package)

        self.cursor.execute('''SELECT * FROM %s WHERE project = ? ORDER BY name;''' % libdbcore.table_srcpackage, (project_id,))
        for row in self.cursor:
            pkg_node = self._get_package_node_from_row(row, ignore_upstream, parent_project)
            prj_node.append(pkg_node)
            try:
                del should_exist[row['name']]
            except KeyError:
                pass

        if len(should_exist) > 0:
            missing_node = ET.Element('missing')
            for (should_package_name, (should_parent_project, should_parent_package)) in should_exist.iteritems():
                missing_pkg_node = ET.Element('package')

                missing_pkg_node.set('name', should_package_name)
                missing_pkg_node.set('parent_project', should_parent_project)
                if should_package_name != should_parent_package:
                    missing_pkg_node.set('parent_package', should_parent_package)

                missing_node.append(missing_pkg_node)

            prj_node.append(missing_node)

        if write_cache:
            self._write_cache(project, prj_node)

        return prj_node

    def _get_cache_path(self, project):
        return os.path.join(self.cache_dir, project + '.xml')

    def _read_cache(self, project):
        cache = self._get_cache_path(project)

        try:
            if os.path.exists(cache):
                return ET.parse(cache).getroot()
        except:
            pass

        return None

    def _write_cache(self, project, node):
        cache = self._get_cache_path(project)

        try:
            if os.path.exists(cache):
                return

            dirname = os.path.dirname(cache)
            if not os.path.exists(dirname):
                os.makedirs(dirname)

            if not os.access(dirname, os.W_OK):
                return

            tree = ET.ElementTree(node)
            tree.write(cache)
        except:
            pass

    def create_cache(self, verbose = False):
        try:
            if not os.path.exists(self.cache_dir):
                os.makedirs(self.cache_dir)
        except Exception, e:
            raise InfoXmlException('Cannot create cache directory (%s).' % e)

        if not os.access(self.cache_dir, os.W_OK):
            raise InfoXmlException('No write access.')

        self.cursor.execute('''SELECT name FROM %s;''' % libdbcore.table_project)
        # We need to first take all names because cursor will be re-used
        projects = [ row['name'] for row in self.cursor ]

        # Create the cache containing version of all packages. This will help
        # us avoid doing many small SQL queries, which is really slow.
        #
        # The main difference is that we do one SQL query + many hash accesses,
        # vs 2*(total number of packages in the database) SQL queries. On a
        # test run, the difference results in ~1min15s vs ~5s. That's a 15x
        # time win.
        self.version_cache = {}
        for project in projects:
            self.version_cache[project] = {}
        self.cursor.execute('''SELECT A.name, A.version, B.name AS project FROM %s AS A, %s AS B WHERE A.project = B.id;''' % (libdbcore.table_srcpackage, libdbcore.table_project))
        for row in self.cursor:
            self.version_cache[row['project']][row['name']] = row['version']

        for project in projects:
            self.get_project_node(project, write_cache = True)
            if verbose:
                print 'Wrote cache for %s.' % project

#if __name__ == '__main__':
#    try:
#        info = InfoXml()
#        info.cache_dir = XML_CACHE_DIR + '-test'
#        info.create_cache()
#    except KeyboardInterrupt:
#        pass
