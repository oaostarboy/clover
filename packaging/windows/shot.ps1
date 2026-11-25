param([string]$Path)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
New-Item -ItemType Directory -Force (Split-Path $Path) | Out-Null
$bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
"saved $Path ($($b.Width)x$($b.Height))"
