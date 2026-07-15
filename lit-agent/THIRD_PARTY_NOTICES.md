# Third-party notices

Parts of lit-agent's session persistence, publisher routing, and MCP adapter design were inspired by:

- https://github.com/deathcats4/instsci-workflow
- https://github.com/Rimagination/instsci

The referenced projects are distributed under the MIT License. Their modules were used as design references and adapted to lit-agent's existing CLI, download cascade, storage, and LLM execution model.

## CloakBrowser runtime dependency

The MDPI/Elsevier visible-browser route depends on [CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser), installed separately from PyPI rather than vendored in this repository.

- The Python/JavaScript wrapper is MIT licensed.
- The compiled Chromium binary has separate terms and is not redistributed by lit-agent. See CloakBrowser's [BINARY-LICENSE.md](https://github.com/CloakHQ/CloakBrowser/blob/main/BINARY-LICENSE.md).

## InstSci license

MIT License

Copyright (c) 2025 InstSci contributors

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
