"""
DistroNomad - Linux Dağıtım Taşıma, Yedekleme ve Konfigürasyon Yöneticisi.
"""

import logging

__version__ = "0.9.0"
__app_name__ = "DistroNomad"

# Kütüphane / TUI için varsayılan NullHandler (TUI ekranına log basılmasını önler)
logging.getLogger(__name__).addHandler(logging.NullHandler())
