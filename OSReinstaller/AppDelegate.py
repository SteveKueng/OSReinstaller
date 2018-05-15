# -*- coding: utf-8 -*-
#
#  AppDelegate.py
#  OSReinstaller
#
#  Created by Steve Küng on 07.05.18.
#  Copyright (c) 2018 Steve Küng. All rights reserved.
#

from Foundation import *
from AppKit import *
from objc import YES, NO, IBAction, IBOutlet, nil

class AppDelegate(NSObject):
    
    mainController = IBOutlet()
    
    def applicationDidFinishLaunching_(self, sender):
        NSLog("Application did finish launching.")
        if self.mainController:
            self.mainController.start()
