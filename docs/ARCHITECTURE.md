# Code architecture

Raw MRI Data Explorer is a local Qt desktop application for Siemens Twix RAWDATA.
Its reader and numerical helpers can be used independently of the desktop UI.

## Module map

| Module | Responsibility |
| --- | --- |
| `siemens_explorer/twix.py` | Validate VB/VD/VE containers, parse protocol buffers, index MDHs, and lazily read individual channel payloads |
| `analysis.py` | Filter one acquisition group and assemble bounded Cartesian preview arrays |
| `reconstruction.py` | Centered inverse FFT, bounded coil-response estimation, receiver-aligned noise covariance, and adaptive weights |
| `noise.py` | Raw acquisition-by-sample noise matrices without spatial reconstruction |
| `ismrmrd_export.py` | Stream indexed ADCs into an atomic ISMRMRD HDF5 export |
| `qt_app.py` | Own the active dataset, synchronize selection, schedule work, and route exports |
| `qt_models.py` | Read-only pandas table models, stable sorting/filtering, and cooperative background workers |
| `qt_plots.py` | Signal/image presentation, volume planes, histogram levels, and plot exports |
| `qt_blocks.py` | Bounded file/acquisition/channel diagrams using header metadata only |
| `qt_channels.py`, `qt_counters.py`, `qt_wheel.py` | Channel ordinals, observed counter values, and accumulated wheel navigation |
| `qt_headers.py` | Searchable header hierarchy and original-source navigation |
| `qt_theme.py`, `qt_glyphs.py`, `qt_branding.py` | Validated appearance snapshots, editable artwork, and icon rasterization |
| `qt_chrome.py`, `qt_dialogs.py`, `qt_tabs.py` | Custom captions/docks, consistent dialogs, and tab separators |
| `preferences.py`, `startup.py`, `__main__.py` | User settings, themed startup/error handling, and application entry |
| `windows_shortcut.py` | Refresh matching local shortcut icons without resetting Explorer |

Paths in the table are relative to `siemens_explorer/` after the first entry.

## Reader contracts

`TwixFile` owns a read-only handle and should be closed explicitly or used as a
context manager. Its lock serializes seek/read operations. Indexing reads MDHs
and protocol text; payload access is deferred until `read_signal` is called.

`Measurement.offset`, `data_offset`, and `Scan.offset` are absolute byte offsets.
Lengths are bytes. Measurement index and scanner measurement ID are different
identities. A scan's parser index is stable through UI table sorting and filtering.

Acquisition counters remain zero-based scanner values. Channels are selected by
their stored ordinal, while `channel_ids` exposes physical receiver identities.
Repeated counts alone do not establish consistent channel identity. Original
`eval_mask` bits remain available even when a flag name is unknown.

The reader converts timestamp ticks to milliseconds but otherwise retains
protocol units. Signal arrays are owned little-endian complex64 readouts. The
reader does not reverse reflected lines or apply FFTs, scaling, or corrections.

`ScanIndex.complete` means a valid ACQEND marker was found. Limits, truncation,
and cooperative cancellation retain a valid prefix and explicit warnings. A
partial index is useful for inspection and must remain labeled as partial.

## Numerical contracts

Cartesian previews use Y/X order for matrices and Z/Y/X for volumes: X is readout,
Y is phase-encode line, and Z is partition encoding. An inverse FFT along Z
gathers partitions for one slice/counter group; separate 2D slices are not a Z
encoding axis. Raw partition counters and reconstructed plane indices differ.

Matrix bounds are inferred from MDH centers and observed counters. Missing
positions remain zero and repeated positions are averaged. `coverage` identifies
acquired matrix positions; it does not prove scan completeness or acceleration.
Memory estimates precede payload allocation, including FFT temporaries.

`Preview.kspace` contains the selected coil. `Preview.image` contains the displayed
magnitude for the selected FFT axes and channel-combination method. Adaptive
combination uses coarse response estimates and an optional measured covariance,
streaming coils in two passes. Metadata records measured versus assumed noise and
RSS fallback. Noise previews reuse `Preview` with acquisition/sample axes and
`display_kind='noise'`; their arrays are not reconstructed images.

## UI and worker lifecycle

`MainWindow` owns one active `TwixFile` and one background job at a time. Widgets
and model changes stay on the GUI thread. `Worker` runs a function accepting a
cancellation event and progress callback; its signals are queued to UI slots.

Opening a file first builds a candidate directory/index. The previous dataset is
replaced only after success. Closing during a job requests cancellation and
defers handle cleanup until the worker finishes. This prevents reads from a
closed file handle.

Preview selection changes increment a revision number and restart a short
debounce timer. Results with an old revision are discarded. The latest queued
selection runs after the active worker releases its state. A bounded LRU retains
up to eight small previews totaling 32 MiB; volumes above that budget are not cached.

## Export and appearance

CSV export follows the visible proxy order. ISMRMRD export instead uses the full
indexed ADC set, independent of display filters and FFT settings. It preserves
samples, channel identities, and original MDHs; unsupported non-ADC records are
counted. It writes to a sibling temporary file, closes HDF5, then atomically
replaces the destination. Unknown geometry/timing remains explicit rather than
being invented. Source paths and source hard links are protected from overwrite.

Themes load JSON/QSS into a validated snapshot before changing the running UI.
Reloading assets recolors existing graphics while retaining data, selection,
zoom, and levels. Root `logo.svg` is the editable source-checkout identity; the
package has a bundled fallback. Versioned ICO files prevent shortcut icon staleness.
QSettings stores appearance/window layout separately from raw-data content.

## Verification

The test suite builds its own small VB/VD fixtures and malformed inputs, checks
numerical reconstruction properties, and exercises Qt navigation/exports. The
module-isolated runner in `tools/run_tests.py` is shared by contributors and CI.
Real scanner files and local validation artifacts belong outside Git.
