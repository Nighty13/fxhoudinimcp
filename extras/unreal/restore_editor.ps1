# Restore and focus the Unreal Editor window so viewport screenshots are actually rendered.
# A minimized editor stops drawing, and HighResShot / take_high_res_screenshot then write nothing.
Add-Type @"
using System; using System.Runtime.InteropServices;
public class UeWin {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
}
"@
$p = Get-Process UnrealEditor | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Output "Unreal Editor is not running"; exit 1 }
[UeWin]::ShowWindow($p.MainWindowHandle, 9) | Out-Null          # SW_RESTORE
[UeWin]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)               # Alt tap lifts the foreground lock
[UeWin]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[UeWin]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Write-Output "Unreal Editor restored"
