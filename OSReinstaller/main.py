# -*- coding: utf-8 -*-
#
#  main.py
#  OSReinstaller
#
#  Created by Steve Küng on 07.05.18.
#  Copyright (c) 2018 Steve Küng. All rights reserved.
#

# import modules required by application
import objc
import Foundation
import AppKit

from PyObjCTools import AppHelper

# import modules containing classes required to start application and load MainMenu.nib
import AppDelegate

# pass control to AppKit
AppHelper.runEventLoop()
