# General SOP for nanodevice layout

Use this procedure with the device SOP and the existing KlayoutClaw skills.
The device SOP supplies the material roles, layout constraints, mask ownership
and acceptance criteria. Record explicit task overrides with the job inputs.

## 1. Confirm inputs and protect the working layout

- Record the template or prepared layout, output location, physical units and
  process rules. For optical jobs, collect raw final-stack images, physical
  pixel scales, fiducials and any transfer photos or boundary annotations.
- Identify the template's actual marker and electrode layers by inspecting its
  geometry. Check that the chosen material, mask and helper layers use
  distinct layer/datatype pairs and are free where new shapes will be written.
- Verify the live connection and work in a task-owned layout. Before loading
  a template with `commit_gds.py`, verify that a fresh task-owned tab is active.
  Keep input files and prior accepted revisions available for recovery.

## 2. Register final-stack images

- Fit the lowest useful magnification to the template in physical coordinates,
  then align finer final-stack images to the same frame. Record the pixel
  scales, transforms, fiducials and residuals. Check overlays near the device
  as well as across the marker field.
- Check that any contrast-enhanced companion matches its raw image before
  using it to trace edges. Keep the raw image for reviewing the final overlay.
- Use the GDS alignment skill's marker extraction, detection and transform
  fitting to prepare a validated warp. Commit it with the material traces in
  the next stage. With a prepared layout, verify the existing material layers
  and calibration before designing against them.

## 3. Identify, trace and commit material roles

- Compare consecutive transfer photos after establishing their ordering,
  mirror convention and alignment. Newly appearing or persistent boundaries
  help separate flakes of the same material. Several flakes may enter in one
  step, so check competing identities against the final-stack evidence.
- Trace design-critical edges on the registered final-stack images. Use
  transfer photos for identity, and retain the final-stack registration for
  fabrication coordinates. Record visible and inferred edges, uncertainties
  and user corrections for each role. Calibrate any user annotation to the
  raw source image before using its coordinates.
- Follow the Skill's PREPARE handoff for a device SOP with a material map.
  Keep each physically distinct role separate in the traces. Verify that
  committed geometry is on the intended layers and inspect it over the raw
  image. Use the committed polygons in GDS coordinates for design.

## 4. Design and route under the device rules

- Derive feasible placements from the material intersections and exclusions
  required by the device. Choose contacts and their pad assignments together
  so the placement has a viable fan-out. Use calibrated distances and include
  the stated boundary and process tolerances.
- Inspect the actual template electrode interiors. Preview routing, check
  the net assignments and then verify connectivity, spacing and crossings
  on the committed geometry. Preserve mask polarity and allocate each
  opening to its declared process step.
- If a unit, identity, process assumption or mandatory constraint remains
  unresolved, preserve the evidence and ask for the decision needed to proceed.

## 5. Verify and deliver

- Check the working layout against the device SOP. Where the process removes
  material, inspect the resulting retained regions and their electrical
  connectivity. Treat evaluator scores as diagnostics alongside required checks.
- Save the working session with its image placements and references. Reopen
  the saved session to confirm the intended layout and overlays are recoverable.
- Export the fabrication layers and any explicitly required template layers.
  Independently reread the exported file and verify units, layer pairs, mask
  geometry and the required topology. Compare each exported layer to the
  validated working geometry using integer-grid region operations.
- Deliver the mask file, layer/process map and validation record with the
  working session and useful previews. Record uncertainties and distinguish
  geometry validation from experimental process qualification.
