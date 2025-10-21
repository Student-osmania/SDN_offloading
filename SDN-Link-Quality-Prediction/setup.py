#!/usr/bin/env python3
"""
Setup script for SDN-Based LTE-WiFi HetNet project
Checks dependencies and creates required directories
"""

import os
import sys
import subprocess

REQUIRED_DIRS = ['results', 'logs', 'data', 'config']
REQUIRED_PACKAGES = ['ryu', 'mininet', 'tensorflow', 'numpy', 'requests']

def check_python_version():
    if sys.version_info < (3, 8):
        print("ERROR: Python 3.8+ required")
        sys.exit(1)
    print(f"✓ Python {sys.version_info.major}.{sys.version_info.minor}")

def check_package(package):
    try:
        __import__(package)
        print(f"✓ {package}")
        return True
    except ImportError:
        print(f"✗ {package} (missing)")
        return False

def create_directories():
    for d in REQUIRED_DIRS:
        os.makedirs(d, exist_ok=True)
        print(f"✓ Created/verified: {d}/")

def check_mininet():
    try:
        result = subprocess.run(['mn', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✓ Mininet installed")
            return True
    except FileNotFoundError:
        pass
    print("✗ Mininet not found")
    return False

def check_ryu():
    try:
        result = subprocess.run(['ryu-manager', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✓ Ryu controller installed")
            return True
    except FileNotFoundError:
        pass
    print("✗ Ryu not found")
    return False

def main():
    print("=" * 50)
    print("SDN HetNet Environment Setup")
    print("=" * 50)
    
    check_python_version()
    
    print("\nChecking Python packages...")
    all_ok = True
    for pkg in REQUIRED_PACKAGES:
        if not check_package(pkg):
            all_ok = False
    
    print("\nChecking system tools...")
    check_mininet()
    check_ryu()
    
    print("\nCreating directories...")
    create_directories()
    
    print("\n" + "=" * 50)
    if all_ok:
        print("✓ Setup complete!")
    else:
        print("⚠ Some packages missing. Install with:")
        print("  pip3 install ryu tensorflow numpy requests")
        print("  sudo apt-get install mininet")
    print("=" * 50)

if __name__ == '__main__':
    main()