# -*- coding: utf-8 -*-
#
#  MainController.py
#  OSReinstaller
#
#  Created by Steve Küng on 08.05.18.
#  Copyright (c) 2018 Steve Küng. All rights reserved.
#

from Foundation import *
import objc
import os
import urlparse
import urllib
import sys
import argparse
import plistlib
import subprocess
import AppKit
import time
import PyObjCTools

from xml.dom import minidom
from xml.parsers.expat import ExpatError

class ReplicationError(Exception):
        '''A custom error when replication fails'''
        pass

class MainController(NSObject):
    mainWindow = objc.IBOutlet()
    
    versionLabel = objc.IBOutlet()
    buildLabel = objc.IBOutlet()
    postDateLabel = objc.IBOutlet()
    titleLabel = objc.IBOutlet()
    progressIndicator = objc.IBOutlet()
    downloadLabel = objc.IBOutlet()
    infoLabel = objc.IBOutlet()
    
    workdir = '/tmp/OSReinstaller'
    
    DEFAULT_SUCATALOG = (
                         'https://reposado.srgssr.ch/content/catalogs/others/'
                         'index-10.13-10.12-10.11-10.10-10.9'
                         '-mountainlion-lion-snowleopard-leopard.merged-1_3_Fast.sucatalog')
    

    def make_sparse_image(self, volume_name, output_path):
        '''Make a sparse disk image we can install a product to'''
        cmd = ['/usr/bin/hdiutil', 'create', '-size', '8g', '-fs', 'HFS+',
            '-volname', volume_name, '-type', 'SPARSE', '-plist', output_path]
        try:
            output = subprocess.check_output(cmd)
        except subprocess.CalledProcessError, err:
            print >> sys.stderr, err
            self.errorPanel(err)
        try:
            return plistlib.readPlistFromString(output)[0]
        except IndexError, err:
            print >> sys.stderr, 'Unexpected output from hdiutil: %s' % output
            self.errorPanel('Unexpected output from hdiutil: %s' % output)
        except ExpatError, err:
            print >> sys.stderr, 'Malformed output from hdiutil: %s' % output
            print >> sys.stderr, err
            self.errorPanel('Malformed output from hdiutil: %s' % output)


    def make_compressed_dmg(self, app_path, diskimagepath):
        """Returns path to newly-created compressed r/o disk image containing
        Install macOS.app"""

        print ('Making read-only compressed disk image containing %s...'
            % os.path.basename(app_path))
        cmd = ['/usr/bin/hdiutil', 'create', '-fs', 'HFS+',
            '-srcfolder', app_path, diskimagepath]
        try:
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError, err:
            print >> sys.stderr, err
        else:
            print 'Disk image created at: %s' % diskimagepath


    def mountdmg(self, dmgpath):
        """
        Attempts to mount the dmg at dmgpath and returns first mountpoint
        """
        mountpoints = []
        dmgname = os.path.basename(dmgpath)
        cmd = ['/usr/bin/hdiutil', 'attach', dmgpath,
            '-mountRandom', '/tmp', '-nobrowse', '-plist',
            '-owners', 'on']
        proc = subprocess.Popen(cmd, bufsize=-1,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (pliststr, err) = proc.communicate()
        if proc.returncode:
            print >> sys.stderr, 'Error: "%s" while mounting %s.' % (err, dmgname)
            return None
        if pliststr:
            plist = plistlib.readPlistFromString(pliststr)
            for entity in plist['system-entities']:
                if 'mount-point' in entity:
                    mountpoints.append(entity['mount-point'])

        return mountpoints[0]


    def unmountdmg(self, mountpoint):
        """
        Unmounts the dmg at mountpoint
        """
        proc = subprocess.Popen(['/usr/bin/hdiutil', 'detach', mountpoint],
                                bufsize=-1, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        (dummy_output, err) = proc.communicate()
        if proc.returncode:
            print >> sys.stderr, 'Polite unmount failed: %s' % err
            print >> sys.stderr, 'Attempting to force unmount %s' % mountpoint
            # try forcing the unmount
            retcode = subprocess.call(['/usr/bin/hdiutil', 'detach', mountpoint,
                                    '-force'])
            if retcode:
                print >> sys.stderr, 'Failed to unmount %s' % mountpoint


    def install_product(self, dist_path, target_vol):
        '''Install a product to a target volume.
        Returns a boolean to indicate success or failure.'''
        cmd = ['/usr/sbin/installer', '-pkg', dist_path, '-target', target_vol, '-verboseR']
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        while proc.poll() is None:
            output = proc.stdout.readline().strip().decode('UTF-8')
            if output.startswith("installer:"):
                msg = output[10:].rstrip("\n")
                if msg.startswith("PHASE:"):
                    phase = msg[6:]
                    if phase:
                        print phase
                        self.infoLabel.setStringValue_(u''.join(phase))
                elif msg.startswith("STATUS:"):
                    status = msg[7:]
                    if status:
                        print status
                        self.downloadLabel.setStringValue_(u''.join(status))
                elif msg.startswith("%"):
                    percent = float(msg[1:])
                    self.updateProgress_(percent)
        
        if proc.returncode == 0:
            return True
        return False

    def report(self, count, blockSize, totalSize):
        percent = float(count*blockSize*100/totalSize)
        self.performSelectorOnMainThread_withObject_waitUntilDone_(self.updateProgress_, percent, objc.NO)

    def replicate_url(self, full_url, root_dir='/tmp', ignore_cache=False):
        '''Downloads a URL and stores it in the same relative path on our
        filesystem. Returns a path to the replicated file.'''

        path = urlparse.urlsplit(full_url)[2]
        relative_url = path.lstrip('/')
        relative_url = os.path.normpath(relative_url)
        local_file_path = os.path.join(root_dir, relative_url)
        print "Downloading %s..." % full_url

        if not os.path.exists(os.path.dirname(local_file_path)):
            try:
                os.makedirs(os.path.dirname(local_file_path))
            except OSError as exc: # Guard against race condition
                if exc.errno != errno.EEXIST:
                    raise
        loading = "%s..." % full_url.rsplit('/', 1)[-1]
        self.downloadLabel.setStringValue_(loading)
        try:
            urllib.urlretrieve(full_url, local_file_path, reporthook=self.report)
        except Exception as e:
            raise ReplicationError(e)
        return local_file_path


    def parse_server_metadata(self, filename):
        '''Parses a softwareupdate server metadata file, looking for information
        of interest.
        Returns a dictionary containing title, version, and description.'''
        title = ''
        vers = ''
        try:
            md_plist = plistlib.readPlist(filename)
        except (OSError, IOError, ExpatError), err:
            print >> sys.stderr, 'Error reading %s: %s' % (filename, err)
            return {}
        vers = md_plist.get('CFBundleShortVersionString', '')
        localization = md_plist.get('localization', {})
        preferred_localization = (localization.get('English') or
                                localization.get('en'))
        if preferred_localization:
            title = preferred_localization.get('title', '')

        metadata = {}
        metadata['title'] = title
        metadata['version'] = vers
        return metadata


    def get_server_metadata(self, catalog, product_key, workdir, ignore_cache=False):
        '''Replicate ServerMetaData'''
        try:
            url = catalog['Products'][product_key]['ServerMetadataURL']
            try:
                smd_path = self.replicate_url(
                    url, root_dir=workdir, ignore_cache=ignore_cache)
                return smd_path
            except ReplicationError, err:
                print >> sys.stderr, (
                    'Could not replicate %s: %s' % (url, err))
                return None
        except KeyError:
            print >> sys.stderr, 'Malformed catalog.'
            return None


    def parse_dist(self, filename):
        '''Parses a softwareupdate dist file, returning a dict of info of
        interest'''
        dist_info = {}
        try:
            dom = minidom.parse(filename)
        except ExpatError:
            print >> sys.stderr, 'Invalid XML in %s' % filename
            return dist_info
        except IOError, err:
            print >> sys.stderr, 'Error reading %s: %s' % (filename, err)
            return dist_info

        auxinfos = dom.getElementsByTagName('auxinfo')
        if not auxinfos:
            return dist_info
        auxinfo = auxinfos[0]
        key = None
        value = None
        children = auxinfo.childNodes
        # handle the possibility that keys from auxinfo may be nested
        # within a 'dict' element
        dict_nodes = [n for n in auxinfo.childNodes
                    if n.nodeType == n.ELEMENT_NODE and
                    n.tagName == 'dict']
        if dict_nodes:
            children = dict_nodes[0].childNodes
        for node in children:
            if node.nodeType == node.ELEMENT_NODE and node.tagName == 'key':
                key = node.firstChild.wholeText
            if node.nodeType == node.ELEMENT_NODE and node.tagName == 'string':
                value = node.firstChild.wholeText
            if key and value:
                dist_info[key] = value
                key = None
                value = None
        return dist_info


    def download_and_parse_sucatalog(self, sucatalog, workdir, ignore_cache=False):
        '''Downloads and returns a parsed softwareupdate catalog'''
        try:
            localcatalogpath = self.replicate_url(
                sucatalog, root_dir=workdir, ignore_cache=ignore_cache)
        except ReplicationError, err:
            print >> sys.stderr, 'Could not replicate %s: %s' % (sucatalog, err)
            self.errorPanel('Could not replicate %s: %s' % (sucatalog, err))
        try:
            catalog = plistlib.readPlist(localcatalogpath)
            return catalog
        except (OSError, IOError, ExpatError), err:
            print >> sys.stderr, (
                'Error reading %s: %s' % (localcatalogpath, err))
            self.errorPanel('Error reading %s: %s' % (localcatalogpath, err))


    def find_mac_os_installers(self, catalog):
        '''Return a list of product identifiers for what appear to be macOS
        installers'''
        mac_os_installer_products = []
        if 'Products' in catalog:
            product_keys = list(catalog['Products'].keys())
            for product_key in product_keys:
                product = catalog['Products'][product_key]
                try:
                    if product['ExtendedMetaInfo'][
                            'InstallAssistantPackageIdentifiers'][
                                'OSInstall'] == 'com.apple.mpkg.OSInstall':
                        mac_os_installer_products.append(product_key)
                except KeyError:
                    continue
        return mac_os_installer_products


    def os_installer_product_info(self, catalog, workdir, ignore_cache=False):
        '''Returns a dict of info about products that look like macOS installers'''
        product_info = {}
        installer_products = self.find_mac_os_installers(catalog)
        for product_key in installer_products:
            product_info[product_key] = {}
            filename = self.get_server_metadata(catalog, product_key, workdir)
            product_info[product_key] = self.parse_server_metadata(filename)
            product = catalog['Products'][product_key]
            product_info[product_key]['PostDate'] = str(product['PostDate'])
            distributions = product['Distributions']
            dist_url = distributions.get('English') or distributions.get('en')
            try:
                dist_path = self.replicate_url(
                    dist_url, root_dir=workdir, ignore_cache=ignore_cache)
            except ReplicationError, err:
                print >> sys.stderr, 'Could not replicate %s: %s' % (dist_url, err)
            dist_info = self.parse_dist(dist_path)
            product_info[product_key]['DistributionPath'] = dist_path
            product_info[product_key].update(dist_info)
        return product_info


    def replicate_product(self, catalog, product_id, workdir, ignore_cache=False):
        '''Downloads all the packages for a product'''
        product = catalog['Products'][product_id]
        product_count = len(product.get('Packages', []))
        for idx,package in enumerate(product.get('Packages', [])):
            # TO-DO: Check 'Size' attribute and make sure
            # we have enough space on the target
            # filesystem before attempting to download
            self.infoLabel.setStringValue_("Downloading %i of %i" % (idx, product_count))
            if 'URL' in package:
                try:
                    self.replicate_url(
                        package['URL'], root_dir=workdir,
                        ignore_cache=ignore_cache)
                except ReplicationError, err:
                    print >> sys.stderr, (
                        'Could not replicate %s: %s' % (package['URL'], err))
                    self.errorPanel('Could not replicate %s: %s' % (package['URL'], err))
            if 'MetadataURL' in package:
                try:
                    self.replicate_url(package['MetadataURL'], root_dir=workdir,
                                ignore_cache=ignore_cache)
                except ReplicationError, err:
                    print >> sys.stderr, (
                        'Could not replicate %s: %s'
                        % (package['MetadataURL'], err))
                    self.errorPanel('Could not replicate %s: %s'
                        % (package['MetadataURL'], err))
    

    def find_install_macos_app(self, dir_path):
        '''Returns the path to the first Install macOS.app found the top level of
        dir_path, or None'''
        for item in os.listdir(dir_path):
            item_path = os.path.join(dir_path, item)
            startosinstall_path = os.path.join(
                item_path, 'Contents/Resources/startosinstall')
            if os.path.exists(startosinstall_path):
                return item_path
        # if we get here we didn't find one
        return None


    def reinstallOS(self, startosinstall_path, macos_app):
        this_dir = os.path.dirname(os.path.abspath(__file__))
        ptyexec_path = os.path.join(this_dir, 'ptyexec')
        if os.path.exists(ptyexec_path):
            cmd = [ptyexec_path]
        else:
            # fall back to /usr/bin/script
            # this is not preferred because it uses way too much CPU
            # checking stdin for input that will never come...
            cmd = ['/usr/bin/script', '-q', '-t', '1', '/dev/null']

        cmd.extend([startosinstall_path, "--applicationpath", macos_app, "--eraseinstall", "--agreetolicense", "--nointeraction"])

        # more magic to get startosinstall to not buffer its output for
        # percent complete
        env = {'NSUnbufferedIO': 'YES'}

        proc = subprocess.Popen(
            cmd, shell=False, bufsize=-1, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        startosinstall_output = []
        while True:
            output = proc.stdout.readline()
            if not output and (proc.returncode != None):
                break
            info_output = output.rstrip('\n').decode('UTF-8')
            # save all startosinstall output in case there is
            # an error so we can dump it to the log
            startosinstall_output.append(info_output)

            # parse output for useful progress info
            msg = info_output.rstrip('\n')
            if msg.startswith('Preparing to '):
                print msg
                self.infoLabel.setStringValue_(u''.join(msg))
            elif msg.startswith('Preparing '):
                # percent-complete messages
                try:
                    percent = int(float(msg[10:].rstrip().rstrip('.')))
                except ValueError:
                    percent = -1
                self.updateProgress_(percent)
            elif msg.startswith(('By using the agreetolicense option',
                                'If you do not agree,')):
                # annoying legalese
                pass
            elif msg.startswith('Helper tool cr'):
                # no need to print that stupid message to screen!
                # 10.12: 'Helper tool creashed'
                # 10.13: 'Helper tool crashed'
                print msg
            elif msg.startswith(
                    ('Signaling PID:', 'Waiting to reboot',
                    'Process signaled okay')):
                # messages around the SIGUSR1 signalling
                print msg
            elif msg.startswith('System going down for install'):
                msg = 'System will restart and begin install of macOS.'
                print msg
                self.infoLabel.setStringValue_(u''.join(msg))
                self.updateProgress_(100)
                self.downloadLabel.setStringValue_(u''.join("done"))
            else:
                # none of the above, just display
                print msg
                self.downloadLabel.setStringValue_(u''.join(msg))


    def updateProgress_(self, value):
        '''UI stuff should be done on the main thread. Yet we do all our interesting work
        on a secondary thread. So to update the UI, the secondary thread should call this
        method using performSelectorOnMainThread_withObject_waitUntilDone_'''
        self.progressIndicator.setDoubleValue_(value)


    def errorPanel(self, error):
        alert = NSAlert.alertWithMessageText_defaultButton_alternateButton_otherButton_informativeTextWithFormat_(
            NSLocalizedString(error, None),
            NSLocalizedString(u"Quit", None),
            objc.nil,
            objc.nil,
            NSLocalizedString(u"", None))
        
        alert.beginSheetModalForWindow_modalDelegate_didEndSelector_contextInfo_(
            self.mainWindow, self, self.errorPanelDidEnd_returnCode_contextInfo_, objc.nil)

    def noSleep(self):
        print('Running \'caffeinate\' on MacOSX to prevent the system from sleeping')
        subprocess.Popen(['caffeinate', '-d', '-i'])

    @PyObjCTools.AppHelper.endSheetMethod
    def errorPanelDidEnd_returnCode_contextInfo_(self, alert, returncode, contextinfo):
        # 0 = reload workflows
        # 1 = Restart
        if returncode == 0:
            pass


    def startReinstaller(self):
        self.mainWindow.setCanBecomeVisibleWithoutLogin_(True)
        self.mainWindow.setLevel_(AppKit.NSScreenSaverWindowLevel)
        self.mainWindow.makeKeyAndOrderFront_(self)

        self.noSleep()

        catalog = self.download_and_parse_sucatalog(self.DEFAULT_SUCATALOG, self.workdir)
        product_info = self.os_installer_product_info(catalog, self.workdir)
        newest_item =  product_info.itervalues().next()
        
        self.titleLabel.setStringValue_(newest_item.get("title"))
        self.versionLabel.setStringValue_(newest_item.get("version"))
        self.buildLabel.setStringValue_(newest_item.get("BUILD"))
        self.postDateLabel.setStringValue_(newest_item.get("PostDate"))

        product_id = product_info.keys()[0]
        self.replicate_product(catalog, product_id, self.workdir)

        # generate a name for the sparseimage
        volname = ('Install_macOS_%s-%s'
                % (product_info[product_id]['version'],
                    product_info[product_id]['BUILD']))
        sparse_diskimage_path = os.path.join(self.workdir, volname + '.sparseimage')
        if os.path.exists(sparse_diskimage_path):
            os.unlink(sparse_diskimage_path)

        # make an empty sparseimage and mount it
        print 'Making empty sparseimage...'
        self.updateProgress_(0)
        self.infoLabel.setStringValue_('Making empty sparseimage...')
        self.downloadLabel.setStringValue_("")
        sparse_diskimage_path = self.make_sparse_image(volname, sparse_diskimage_path)

        self.infoLabel.setStringValue_('Mount sparseimage...')
        self.downloadLabel.setStringValue_("")
        mountpoint = self.mountdmg(sparse_diskimage_path)
        if mountpoint:
            # install the product to the mounted sparseimage volume
            self.infoLabel.setStringValue_('Install the product to the mounted sparseimage...')
            success = self.install_product(
                product_info[product_id]['DistributionPath'],
                mountpoint)
            if not success:
                print >> sys.stderr, 'Product installation failed.'
                self.unmountdmg(mountpoint)
                self.errorPanel('Product installation failed.')
            print 'Product downloaded and installed to %s' % sparse_diskimage_path
            self.infoLabel.setStringValue_('Product downloaded and installed to %s' % sparse_diskimage_path)
            self.downloadLabel.setStringValue_("")
            
             # install os
            print 'Start os reinstall'
            macos_app = self.find_install_macos_app(os.path.join(mountpoint, "Applications"))
            startosinstall_path =  os.path.join(macos_app, 'Contents/Resources/startosinstall')
            if startosinstall_path:
                self.reinstallOS(startosinstall_path, macos_app)
            else:
                print 'startosisntall not found!'
                self.errorPanel('startosisntall not found!')
            

    def start(self):
        NSThread.detachNewThreadSelector_toTarget_withObject_(self.startReinstaller, self, None)
