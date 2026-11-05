# Siemens Raw Data Explorer

A native desktop program built with **Python, Qt (PySide6), NumPy, and pyqtgraph** for exploring Siemens MRI Twix `.dat` files. Its 90s workstation appearance uses classic gray panels, beveled controls, navy selections, and original pixel-style RDX logos. Files are opened read-only and processed on this computer.

## Run

The development environment in this workspace is prepared. Double-click **`start.cmd`**, or run:

```powershell
.\start.ps1
.\start.ps1 -DataFile 'C:\data\meas_MID0001.dat'
```

The native application can also be launched directly:

```powershell
.\.venv\Scripts\python.exe -m siemens_explorer
.\.venv\Scripts\python.exe -m siemens_explorer 'C:\data\meas_MID0001.dat'
.\.venv\Scripts\python.exe -m siemens_explorer --theme dark --windowed
```

`python app.py` is an equivalent entry point. After installing the package with `python -m pip install -e .`, the Windows GUI entry point is `.venv\Scripts\siemens-explorer.exe`. The program does not need a browser, server, account, or API key.

For a fresh checkout, install 64-bit Python 3.10 or newer:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m siemens_explorer
```

The Windows launcher creates `.venv` and installs missing dependencies when necessary. Initial installation requires internet access. On macOS/Linux, use `python3 -m venv .venv` and `.venv/bin/python` instead of the Windows executable paths.

## Workspace

- Open a `.dat` file with the native file dialog, or drag it onto the window.
- Use the dockable explorer to select a measurement, acquisition type, slice, partition, echo, repetition, and additional loop counters.
- Select an individual receive channel from the visible channel browser. Each row shows its ordinal and physical channel ID.
- Header indexing and preview construction run in the background. The status bar shows activity and allows cancellation. The default index limit is **250,000 acquisitions**; a partial index is labeled explicitly, and the limit can be raised before re-indexing.
- Switch between **Overview**, **Acquisitions**, **Signal**, **K-space & image**, **Protocol**, **Raw headers**, and **Data blocks**.
- Previews refresh automatically. Native menus and the toolbar provide file opening, appearance, indexing, and export actions.

| View | Purpose |
| --- | --- |
| Overview | Measurement properties, format, byte offsets, protocol summary, and observed imaging dimensions |
| Acquisitions | Sortable and filterable scan-header table; row selection chooses the signal readout |
| Signal | Magnitude, real/imaginary, phase, or FFT magnitude for an indexed acquisition and selected receive channel |
| K-space & image | Bounded Cartesian preview for a single counter group; optional root-sum-of-squares coil combination |
| Protocol | Searchable ASCCONV paths and scalar XProtocol parameters, preserving stored names and units |
| Raw headers | Original named header-text buffers with search |
| Data blocks | Clickable visual map of file, measurement, acquisition, and channel blocks with byte offsets, lengths, and payload metadata |

All acquisition counters are the scanner's zero-based values. Receive-channel ordinal is the position within an acquisition; physical channel IDs are checked before building a coherent Cartesian preview.

The **Data blocks** view renders a bounded overview of the indexed structures without reading signal payloads. Hover to inspect metadata; click a channel block to select its readout in **Signal**. It does not edit the source file or require a full-file image allocation.

### Browse slices

Use the previous/next buttons, slice slider, or position field above the workspace to move through slices. Positions follow the observed scanner counters, including noncontiguous values; the sidebar selection stays synchronized. The axis selector also supports partitions, selected automatically for datasets with one slice and multiple partitions.

Opening **K-space & image** builds the first preview automatically. Scrolling over an image moves between slices; hold **Ctrl** while scrolling to zoom. Changing slice refreshes the preview in the background. Rapid scrolling keeps the latest requested position, preserving the selected echo, receive channel, coil combination, zoom, and window levels. Controls are disabled when only one position is available.

### Browse receive channels

Click a channel row to view that coil individually. Selection updates the complex readout and image preview automatically and turns off the combined-channel RSS view. The previous/next channel buttons and **Ctrl+Left / Ctrl+Right** move through the available channels while keeping the slice and echo fixed. Physical channel IDs remain visible, including when IDs are noncontiguous.

Use **Combine all channels (RSS)** when a combined magnitude image is useful. Selecting an individual channel returns to its single-channel image. Channel browsing preserves zoom and window levels; rapid changes keep the latest requested channel.

## Export

Exports use native save dialogs:

- Acquisition table: CSV.
- Selected complex readout: CSV with real, imaginary, magnitude, and phase values.
- Measurement metadata and parsed protocol: JSON.
- Original named header buffers: text.
- Cartesian preview: compressed NumPy NPZ, including `kspace`, `image`, `coverage`, `lines`, metadata, and preview qualifications.
- Signal plot or paired k-space/image view: PNG.

NPZ files can be opened with `numpy.load(path, allow_pickle=False)`. Exports preserve data and do not modify the source `.dat` file.

## Controls

| Shortcut | Action |
| --- | --- |
| Ctrl+O / Ctrl+W | Open raw data / close dataset |
| Ctrl+1 through Ctrl+7 | Select a workspace tab |
| Page Up / Page Down | Previous / next slice or partition |
| Ctrl+Left / Ctrl+Right | Previous / next receive channel |
| F5 | Re-index measurement |
| Esc | Cancel the current background operation |
| F11 | Toggle full screen |

Drag inside plots to pan. Scroll over images to navigate slices; use Ctrl+scroll to zoom. Signal plots retain scroll-to-zoom. Image histograms control window/level; **Auto levels** and **Fit images** restore useful display defaults. Appearance can be changed through **View → Appearance → Light / Dark theme**. Both themes retain the compact beveled controls; image panes stay dark. The initial window is maximized unless `--windowed` is supplied.

The original RDX icon is included as scalable SVG artwork and a Windows ICO under `siemens_explorer/resources/`. The application banner, window, and taskbar use this identity.

## Format support and limits

The reader supports **Siemens MRI Twix VB single-measurement and VD/VE multi-RAID layouts**, including 128-byte VB / 192-byte VD/VE acquisition headers, receive-channel IDs, all 14 loop counters, evaluation flags, and complex float32 signal payloads. Samples are read one acquisition/channel at a time instead of loading the entire file. Header sizes and read boundaries are checked. Truncated files retain usable acquisition prefixes, and intact earlier RAID measurements remain accessible when trailing measurements are unavailable.

The `.dat` extension is also used by unrelated products. This program targets MRI Twix; it does not read arbitrary Siemens industrial data, DICOM images, or encrypted/proprietary variants outside these layouts.

The Cartesian image is an **inspection preview**, not a scanner reconstruction. It aligns readouts using MDH center counters, reverses reflected readouts, averages repeated positions, leaves missing samples zero, and applies a centered 2D inverse FFT. Mixed slices, echoes, repetitions, other loop counters, and inconsistent physical receive-channel IDs are rejected.

Displayed matrix bounds are inferred from observed MDH counters. Preview coverage describes the allocated matrix and cannot establish the prescribed matrix, full sampling, or acceleration. Trajectory is not validated. Regridding, ramp-sampling correction, parallel imaging, SMS reconstruction, oversampling removal, noise prewhitening, sensitivity correction, and scanner image orientation are not applied. Non-Cartesian or sequence-specific data require an appropriate reconstruction before interpreting an image. The preview is limited to 2,048 pixels per axis and a conservative 128 MiB estimated working-memory budget.

No real scanner dataset was supplied during development. Verification uses synthetic VB/VD fixtures, malformed/truncated inputs, numerical checks, and Qt integration tests. Compatibility with representative scanner files remains to be checked. This is research and inspection software.

## Python API

```python
from siemens_explorer.twix import TwixFile
from siemens_explorer.analysis import scans_to_frame, select_scans, build_cartesian_preview

with TwixFile("measurement.dat") as raw:
    measurement = raw.measurements[-1]
    index = raw.index_measurement(measurement, max_scans=250_000)
    table = scans_to_frame(index.scans)
    group = select_scans(index.scans, {
        "is_image": True, "slice": 0, "partition": 0, "echo": 0,
        "repetition": 0, "average": 0, "set": 0, "phase": 0,
        "segment": 0, "ida": 0, "idb": 0, "idc": 0, "idd": 0, "ide": 0,
    })
    signal = raw.read_signal(measurement, group[0], channel_index=0)
    preview = build_cartesian_preview(raw, measurement, group, all_channels=True)
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover binary layout variants, multiple measurements, payloads and physical channel IDs, evaluation flags, truncated input, index limits, protocol extraction, FFT recovery, reflection, duplicate averaging, missing lines, mixed groups, allocation guards, and desktop controls/exports.

## Local Git history

The project is tracked in a local Git repository on `main`. Source, logo assets, documentation, and tests belong in the repository; virtual environments, generated caches, exports, and raw `.dat` files are excluded by `.gitignore`. Use `git status` and `git log --oneline` to inspect local changes and history. A remote hosting destination is a separate configuration.

The independent binary reader follows layout definitions documented by [twixtools MDH schemas](https://github.com/pehses/twixtools/blob/master/twixtools/mdh_def.py) and [mapVBVD](https://github.com/pehses/mapVBVD/blob/main/mapVBVD.m). The desktop interface uses [Qt for Python](https://doc.qt.io/qtforpython-6/) and [pyqtgraph](https://www.pyqtgraph.org/).
