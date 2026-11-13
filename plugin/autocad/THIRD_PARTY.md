# Source notes

The ObjectARX/AutoLISP bridge and Python drawing and interchange tools are
adapted from the Qlay_autoCAD prototype. The native bridge used
[chronista-club/autocad-mcp](https://github.com/chronista-club/autocad-mcp) as a
reference (revision `ffe46b136cfadbc61c94baacc43e6782631c5b64`, MIT,
copyright 2026 Mako). The reference implementation is not a runtime
dependency.

Users obtain the [AutoCAD ObjectARX SDK](https://forge.autodesk.com/developer/overview/objectarx-autocad-sdk)
from Autodesk and compile the bundle against their licensed AutoCAD
installation. Python packages are installed separately from
`requirements.txt`.

## Reference license

```text
MIT License

Copyright (c) 2026 Mako

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```
