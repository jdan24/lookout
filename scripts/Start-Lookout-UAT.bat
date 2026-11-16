@echo off
rem ---------------------------------------------------------------------------
rem  Start-Lookout-UAT.bat - starts Lookout in UAT mode (TT ext_uat_cert).
rem  It uses separate UAT credentials, profiles and snapshots, and its own
rem  port, so it can be open next to the Live one. Keep this file in the same
rem  folder as Start-Lookout.bat.
rem ---------------------------------------------------------------------------
call "%~dp0Start-Lookout.bat" uat
