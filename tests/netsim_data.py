"""Sorties de commandes des familles d'équipements réseau, au format de leurs vrais systèmes (d'après leur
documentation et des relevés publiés) : elles nourrissent les tests de lecture et le simulateur de ligne de commande
(netsim_cli). Les noms, numéros de série et adresses sont fictifs."""

IOS_VERSION = """Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E9, RELEASE SOFTWARE (fc1)
Technical Support: http://www.cisco.com/techsupport
Copyright (c) 1986-2023 by Cisco Systems, Inc.
Compiled Wed 26-Jul-23 03:05 by mcpre

ROM: Bootstrap program is C2960X boot loader
BOOTLDR: C2960X Boot Loader (C2960X-HBOOT-M) Version 15.2(7r)E1, RELEASE SOFTWARE (fc1)

SW-CORE uptime is 12 weeks, 3 days, 4 hours, 22 minutes
System returned to ROM by power-on
System restarted at 10:12:33 CET Mon Aug 31 2026
System image file is "flash:/c2960x-universalk9-mz.152-7.E9.bin"
Last reload reason: power-on



This product contains cryptographic features and is subject to United
States and local country laws governing import, export, transfer and
use.

cisco WS-C2960X-48FPD-L (APM86XXX) processor (revision B0) with 524288K bytes of memory.
Processor board ID FOC2133X0AB
Last reset from power-on
2 Virtual Ethernet interfaces
1 FastEthernet interface
52 Gigabit Ethernet interfaces
The password-recovery mechanism is enabled.

512K bytes of flash-simulated non-volatile configuration memory.
Base ethernet MAC Address       : 70:DB:98:12:34:00
Motherboard assembly number     : 73-15898-04
Model revision number           : B0
Model number                    : WS-C2960X-48FPD-L
System serial number            : FOC2133X0AB
Top Assembly Part Number        : 68-4612-02


Switch Ports Model                     SW Version            SW Image
------ ----- -----                     ----------            ----------
*    1 52    WS-C2960X-48FPD-L         15.2(7)E9             C2960X-UNIVERSALK9-M


Configuration register is 0xF
"""

IOS_INVENTORY = """NAME: "1", DESCR: "WS-C2960X-48FPD-L"
PID: WS-C2960X-48FPD-L , VID: V05  , SN: FOC2133X0AB

NAME: "Switch 1 - FlexStackPlus Module", DESCR: "Stacking Module"
PID: C2960X-STACK      , VID: V01  , SN: FOC21330YZ1
"""

IOS_STATUS = """
Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1   AP-Accueil         connected    10         a-full a-1000 10/100/1000BaseTX
Gi1/0/2                      notconnect   1            auto   auto 10/100/1000BaseTX
Gi1/0/3   Imprimante RDC     connected    20         a-full  a-100 10/100/1000BaseTX
Gi1/0/4                      disabled     1            auto   auto 10/100/1000BaseTX
Gi1/0/5                      err-disabled 1            auto   auto 10/100/1000BaseTX
Gi1/0/49  Uplink-Coeur       connected    trunk        full  1000 1000BaseSX SFP
Gi1/0/50                     notconnect   1            full  1000 Not Present
Po1       Uplink             connected    trunk      a-full a-1000
"""

IOS_CPU = "CPU utilization for five seconds: 7%/0%; one minute: 6%; five minutes: 5%"
IOS_MEM = "Processor Pool Total:  401028984 Used:  160411593 Free:  240617391"
IOS_ENV = """FAN is OK
SYSTEM TEMPERATURE is OK
Temperature Value: 38 Degree Celsius
Temperature State: GREEN
Yellow Threshold : 66 Degree Celsius
Red Threshold    : 76 Degree Celsius
POWER is OK
"""
IOS_DIR = """Directory of flash:/

    2  -rwx    26179584  Aug 31 2026 10:01:12 +01:00  c2960x-universalk9-mz.152-7.E9.bin
    3  -rwx        3096  Nov 23 2026 09:40:03 +01:00  vlan.dat

122185728 bytes total (95002624 bytes free)
"""

IOS_RUNNING = """Building configuration...

Current configuration : 1832 bytes
!
! Last configuration change at 09:40:03 CET Mon Nov 23 2026 by admin
! NVRAM config last updated at 09:40:05 CET Mon Nov 23 2026 by admin
!
version 15.2
no service pad
service timestamps debug datetime msec
service timestamps log datetime msec
service password-encryption
!
hostname SW-CORE
!
boot-start-marker
boot system flash:/c2960x-universalk9-mz.152-7.E9.bin
boot-end-marker
!
enable secret 9 $9$abcdefghijklmn$opqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012345
!
username admin privilege 15 secret 9 $9$zyxwvutsrqponm$lkjihgfedcbaZYXWVUTSRQPONMLKJIHGFEDCBA987654
aaa new-model
!
clock timezone CET 1 0
ip domain-name exemple.lan
ip ssh version 2
ip scp server enable
!
spanning-tree mode rapid-pvst
!
vlan 10
 name WIFI
vlan 20
 name IMPRIMANTES
!
interface GigabitEthernet1/0/1
 description AP-Accueil
 switchport access vlan 10
 switchport mode access
!
interface GigabitEthernet1/0/3
 description Imprimante RDC
 switchport access vlan 20
 switchport mode access
!
interface Vlan1
 ip address 192.0.2.2 255.255.255.0
!
ntp server 192.0.2.1
ntp clock-period 36028797
line vty 0 15
 transport input ssh
!
end
"""

XE_VERSION = """Cisco IOS XE Software, Version 17.09.04a
Cisco IOS Software [Cupertino], Catalyst L3 Switch Software (CAT9K_IOSXE), Version 17.9.4a, RELEASE SOFTWARE (fc3)
Technical Support: http://www.cisco.com/techsupport
Copyright (c) 1986-2023 by Cisco Systems, Inc.
Compiled Fri 20-Oct-23 10:44 by mcpre

ROM: IOS-XE ROMMON
BOOTLDR: System Bootstrap, Version 17.11.1r, RELEASE SOFTWARE (P)

C9300-SIEGE uptime is 1 year, 2 weeks, 5 days, 3 hours, 1 minute
Uptime for this control processor is 1 year, 2 weeks, 5 days, 3 hours, 3 minutes
System returned to ROM by Reload Command
System image file is "flash:packages.conf"
Last reload reason: Reload Command

cisco C9300-48P (X86) processor with 1338934K/6147K bytes of memory.
Processor board ID FCW2233L0ZZ
1 Virtual Ethernet interface
56 Gigabit Ethernet interfaces
8 Ten Gigabit Ethernet interfaces
2048K bytes of non-volatile configuration memory.
8388608K bytes of physical memory.

Base Ethernet MAC Address          : 00:a7:42:11:22:00
Model Number                       : C9300-48P
System Serial Number               : FCW2233L0ZZ

Switch Ports Model              SW Version        SW Image              Mode
------ ----- -----              ----------        ----------            ----
*    1 65    C9300-48P          17.09.04a         CAT9K_IOSXE           INSTALL


Configuration register is 0x102
"""

CSR_VERSION = """Cisco IOS XE Software, Version 17.03.08a
Cisco IOS Software [Amsterdam], Virtual XE Software (X86_64_LINUX_IOSD-UNIVERSALK9-M), Version 17.3.8a, RELEASE SOFTWARE (fc3)

RTR-WAN uptime is 3 days, 2 hours, 5 minutes
System image file is "bootflash:packages.conf"

cisco CSR1000V (VXE) processor (revision VXE) with 2071102K/3075K bytes of memory.
Processor board ID 9ABCDEF1234
4 Gigabit Ethernet interfaces
Configuration register is 0x2102
"""

CSR_BRIEF = """Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet1       198.51.100.2    YES NVRAM  up                    up
GigabitEthernet2       192.0.2.1       YES NVRAM  up                    up
GigabitEthernet3       unassigned      YES NVRAM  administratively down down
GigabitEthernet4       unassigned      YES NVRAM  down                  down
Loopback0              10.255.0.1      YES NVRAM  up                    up
"""

SB_VERSION = """Active-image: flash://system/images/image_cbs_ros_3.5.0.22_release_cisco_signed.bin
  Version: 3.5.0.22
  MD5 Digest: 7b1e8f35c3a42f0d7c6d2f6e9b0c1a23
  Date: 23-Sep-2026
  Time: 10:47:31
Inactive-image: flash://system/images/image_cbs_ros_3.4.0.17_release_cisco_signed.bin
  Version: 3.4.0.17
  MD5 Digest: 1a2b3c4d5e6f708192a3b4c5d6e7f809
  Date: 19-Jan-2026
  Time: 08:12:02
"""

SB_SYSTEM = """System Description:                       CBS350-24P-4G 24-Port Gigabit PoE Managed Switch
System Up Time (days,hour:min:sec):       12,03:27:44
System Contact:
System Name:                              SW-ETAGE1
System Location:                          Etage 1
System MAC Address:                       a0:f8:49:5a:8c:3d
System Object ID:                         1.3.6.1.4.1.9.6.1.95.24.5

Main Power Supply Status:                 OK
Fan Status:                               OK
"""

SB_INVENTORY = """NAME: "1"    DESCR: "CBS350-24P-4G 24-Port Gigabit PoE Managed Switch"
PID: CBS350-24P-4G       VID: V02     SN: PSZ24051ABC
"""

SB_STATUS = """                                             Flow Link          Back   Mdix
Port     Type         Duplex  Speed Neg      ctrl State       Pressure Mode
-------- ------------ ------  ----- -------- ---- ----------- -------- -------
gi1/0/1  1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi1/0/2  1G-Copper      --      --     --     --  Down           --     --
gi1/0/3  1G-Copper    Full    100   Enabled  Off  Up          Disabled Off
gi1/0/4  1G-Copper      --      --     --     --  Down           --     --
te1/0/1  10G-Fiber    Full    10000 Disabled Off  Up          Disabled Off

                                          Flow    Link        Back
Ch       Type    Duplex  Speed  Neg      control  State       Pressure
-------- ------- ------  -----  -------- -------  ----------- --------
Po1         --     --      --      --       --    Not Present    --
"""

SB_CPU = """CPU utilization service is on.

CPU utilization
--------------------------------------------------
five seconds: 4%; one minute: 3%; five minutes: 3%
"""

SB_RUNNING = """config-file-header
SW-ETAGE1
v3.5.0.22 / RCBS3.5_930_871_191
CLI v1.0
file SSD indicator encrypted
@
ssd-control-start
ssd config
ssd file passphrase control unrestricted
no ssd file integrity control
ssd-control-end cb0a3fdb1f3a1af4e4430033719968c0
!
vlan database
vlan 10,20
exit
hostname SW-ETAGE1
username admin password encrypted $15$ABCDEFGHIJKL==$abcdefghijklmnopqrstuvwxyz0123456789 privilege 15
ip ssh server
ip ssh password-auth
!
interface vlan 1
 ip address 192.0.2.3 255.255.255.0
!
interface GigabitEthernet1/0/1
 switchport access vlan 10
exit
!
"""

EXOS_VERSION = """Switch      : 800725-00-08 1849G-00123 Rev 8.0 BootROM: 3.4.2.3    IMG: 33.6.1.14
PSU-1       : Internal PSU-1 800725-00-08 1849G-00123
PSU-2       :

Image   : ExtremeXOS version 33.6.1.14 by release-manager
          on Thu Jun  4 20:20:16 EDT 2026
BootROM : 3.4.2.3
Diagnostics : 5.5
"""

EXOS_SWITCH = """
SysName:          X440G2-BAT-A
SysLocation:      Batiment A
SysContact:       support@extremenetworks.com, +1 888 257 3000
System MAC:       00:04:96:9C:AA:01
System Type:      X440G2-48p-10G4

SysHealth check:  Enabled (Normal)
Recovery Mode:    All
System Watchdog:  Enabled

Current Time:     Mon Nov 23 10:47:12 2026
Timezone:         [Auto DST Disabled] GMT Offset: 60 minutes, name is CET.
Boot Time:        Fri Oct  2 08:13:05 2026
Boot Count:       41
Next Reboot:      None scheduled
System UpTime:    52 days 2 hours 34 minutes 7 seconds

Current State:    OPERATIONAL
Image Selected:   primary
Image Booted:     primary
Primary ver:      33.6.1.14
Secondary ver:    32.7.2.19

Config Selected:  primary.cfg
Config Booted:    primary.cfg
"""

EXOS_PORTS = """Port Summary Monitor                                    Mon Nov 23 10:47:15 2026
Port      Display              VLAN Name          Port  Link  Speed  Duplex
#         String               (or # VLANs)       State State Actual Actual
==================================================================================
1         AP-Hall              Default            E     A      1000  FULL
2                              Default            E     R
3         Serveur              (0002)             E     A      1000  FULL
4                              Default            D     R
49                             Default            E     NP
==================================================================================
   Port State: D-Disabled, E-Enabled, L-Licence Disabled
   Link State: A-Active, R-Ready, NP-Port Not Present, L-Loopback
"""

EXOS_CPU = """
      CPU Utilization Statistics - Monitored every 5 seconds
-------------------------------------------------------------------------------

Process      5   10   30   1    5    30   1    Max           Total
            secs secs secs min  mins mins hour            User/System
            util util util util util util util util       CPU Usage
            (%)  (%)  (%)  (%)  (%)  (%)  (%)  (%)         (secs)
-------------------------------------------------------------------------------

System         3.4  3.0  3.1  4.0  3.2  3.1  3.0   41.3       0.00   1503.90
aaa            0.0  0.0  0.0  0.0  0.0  0.0  0.0    0.7      10.25     12.08
"""

EXOS_MEMORY = """System Memory Information
-------------------------
 Total DRAM (KB): 1048576
 System     (KB): 61484
 User       (KB): 563076
 Free       (KB): 424016
"""

EXOS_TEMP = """Field Replaceable Units           Temp (C)   Status   Min  Normal   Max
--------------------------------------------------------------------------
Switch          : X440G2-48p-10G4  41.0      Normal   -10  0-66     67
"""

EXOS_CONFIG = """#
# Module devmgr configuration.
#
configure snmp sysName "X440G2-BAT-A"
configure snmp sysLocation "Batiment A"
#
# Module vlan configuration.
#
configure vlan default delete ports all
create vlan "WIFI"
configure vlan WIFI tag 10
configure vlan WIFI add ports 1 untagged
configure vlan Mgmt ipaddress 192.0.2.4 255.255.255.0
#
# Module exsshd configuration.
#
enable ssh2
"""

VOSS_SYSINFO = """
General Info :

        SysDescr     : VSP-8284XSQ (8.10.1.0)
        SysName      : VSP-COEUR
        SysUpTime    : 52 day(s), 02:34:07
        SysContact   : http://www.extremenetworks.com/contact/
        SysLocation  : Salle serveurs
        SysTime      : Mon Nov 23 10:47:12 2026 CET

Chassis Info:

        Chassis            : 8284XSQ
        ModelName          : 8284XSQ
        BrandName          : Extreme Networks.
        Serial#            : 14JP12345678
        H/W Revision       : 01
        Part Number        : EC8200A2B-E6
        System MAC Address : 00:51:00:ab:cd:00

Temperature Info :

                            Chassis Temperature
                            -------------------
                            43
"""

VOSS_SOFTWARE = """================================================================================
                            software releases in /intflash/release/
================================================================================
VOSS8.10.1.0.GA (Primary Release)
VOSS8.10.0.0.GA (Backup Release)

--------------------------------------------------------------------------------
Auto Commit             : enabled
Commit Timeout          : 10 minutes
"""

VOSS_PORTS = """==========================================================================================
                                     Port Interface
==========================================================================================
PORT                            LINK  PORT           PHYSICAL          STATUS
NUM      INDEX DESCRIPTION      TRAP  LOCK     MTU   ADDRESS           ADMIN  OPERATE
------------------------------------------------------------------------------------------
1/1      192   10GbNone         true  false    1950  00:51:00:ab:cd:00 up     up
1/2      193   10GbNone         true  false    1950  00:51:00:ab:cd:01 up     down
1/3      194   10GbNone         true  false    1950  00:51:00:ab:cd:02 down   down
1/4      195   10GbNone         true  false    1950  00:51:00:ab:cd:03 up     up
"""

VOSS_CPU = """Slot:1
    Current utilization: 9
    1-minute average utilization: 7
    5-minute average utilization: 6
"""
VOSS_MEM = """Slot:1
    Used: 1100112 (KB)
    Free: 2846232 (KB)
    Current utilization: 28 %
"""

VOSS_RUNNING = """Preparing to Display Configuration...
#
# Mon Nov 23 10:47:20 2026 CET
# box type             : VSP-8284XSQ
# software version     : 8.10.1.0
#
config terminal
prompt "VSP-COEUR"
snmp-server name "VSP-COEUR"
vlan create 10 name "WIFI" type port-mstprstp 0
interface GigabitEthernet 1/1
encapsulation dot1q
exit
ssh
end
"""

SNS_PROPERTY = {"Type": "Firewall", "Model": "SN310", "MachineType": "amd64", "Version": "4.3.29",
                "ASQVersion": "8.0.0", "SerialNumber": "SN31XA19D0012345", "HWVersion": "1", "SystemName": "FW-SIEGE"}

FAMILY_OUTPUTS = {
    "cisco-ios": {"show version": IOS_VERSION, "show inventory": IOS_INVENTORY, "show interfaces status": IOS_STATUS,
                  "show processes cpu | include CPU utilization": IOS_CPU, "show processes memory | include Processor Pool": IOS_MEM,
                  "show environment all": IOS_ENV, "dir flash:": IOS_DIR, "show running-config": IOS_RUNNING},
    "cisco-sb": {"show version": SB_VERSION, "show system": SB_SYSTEM, "show inventory": SB_INVENTORY, "show interfaces status": SB_STATUS,
                 "show cpu utilization": SB_CPU, "show running-config": SB_RUNNING},
    "exos": {"show version": EXOS_VERSION, "show switch": EXOS_SWITCH, "show ports no-refresh": EXOS_PORTS, "show cpu-monitoring": EXOS_CPU,
             "show memory": EXOS_MEMORY, "show temperature": EXOS_TEMP, "show configuration": EXOS_CONFIG},
    "voss": {"show sys-info": VOSS_SYSINFO, "show software": VOSS_SOFTWARE, "show interfaces gigabitEthernet": VOSS_PORTS,
             "show khi performance cpu": VOSS_CPU, "show khi performance memory": VOSS_MEM, "show running-config": VOSS_RUNNING},
}
