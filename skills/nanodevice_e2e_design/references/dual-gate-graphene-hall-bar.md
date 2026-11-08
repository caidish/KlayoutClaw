# Dual-gate graphene Hall bar device SOP template

Copy this template and select the copy in the task request. Confirm the actual
stack, etch ownership, placement constraints, and mask outputs before using
the example rules. Derive geometry from the current sample's evidence.

## Stack and material layers

This example tracks six roles in stack order from top to bottom. The two
graphite plates are separate electrical gates. Each hBN plate has its own
boundary. The `graphene` role represents the channel material, including BLG
when applicable.

Use the exact role names as keys under `materials` in `traces.json`. Copy this
object into `material_layer_map.json` for the validated `commit_gds.py` call.
Change the pairs as needed to avoid layers already occupied by the template,
masks or helpers. Each role in the map requires a corresponding contour.

```json
{
  "top_top_hBN": "110/0",
  "top_graphite": "111/0",
  "top_hBN": "112/0",
  "graphene": "113/0",
  "bottom_hBN": "114/0",
  "bottom_graphite": "115/0"
}
```

If the sample adds or omits a layer, update the stack, map, trace roles and
associated device checks together. Use transfer chronology and final-stack
edges to distinguish the two graphite regions before committing them.

## Process and design inputs

| Item | Example or value to confirm |
|---|---|
| Masks | Electrodes `20/0`, shape etch `21/0`, selective top-gate etch `22/0`. Etch polygons denote removal. |
| Contact etch | Example process includes contact etch in the electrode opening. Confirm the layers it removes and the required contact edge for each material. |
| Channel | Set required length and width. Place it in the retained overlap of graphene and both gates after all applicable etches. |
| Hall contacts | Set the number and roles of contacts on each side. Example finger width is 1 µm with at least 0.8 µm same-side spacing. Specify any source/drain roles and whether fingers may enter the original bottom-gate footprint. |
| Gate contacts | Set contact count and choose a separate path from each gate's electrode to that gate over the channel. |
| Routing | Assign distinct chip electrodes, minimum net spacing, contact overlap and allowed lead widening. |
| Tolerances | Set optical edge uncertainty and mask placement tolerance. Record which checks must hold under the allowed shifts. |
| Reference window | If required by the process, specify its size and composition. An example is a window of at least 5 × 5 µm spanning isolated top-top hBN and bare substrate. |

## Construct the masks

1. Place the channel and contacts in a region with enough retained material
   and space for fan-out. Widen leads at their assigned chip electrodes when
   the process calls for greater overlap. Preserve the specified finger widths.
2. Place continuous isolation cuts between neighboring electrodes where
   residual conducting material could connect them. Extend each cut across
   the entire relevant flake, including flakes beyond the main stack, while
   preserving the intended channel and gate supply paths.
3. Selectively remove top graphite near graphene contacts and keep the top
   gate continuous over the channel. Check the relative edges of shape etch
   and top-gate etch under the specified overlay tolerance. Where the design
   calls for an entirely dual-gated channel, choose their overlap so that a
   bottom-gate-only graphene strip cannot remain attached to the channel.
4. If the electrode opening owns the contact etch, exclude that area from
   the separate shape-etch mask. Include any physical etch reference window
   in the selective mask and keep its labels and guides in the working layout.

## Acceptance checks

- Measure finger widths, gaps, net spacing and each lead's overlap with its
  intended chip electrode. Check every metal net for continuity and isolation
  from the others.
- Subtract the applicable mask openings from each conducting material using
  the stated process model. Verify that the intended graphene contacts reach
  the same retained channel and each gate contact reaches its own retained
  gate over the channel. Check that no remaining material bridges unrelated
  nets. Check contact at the retained material edge when the contact etch
  removes the material inside the opening.
- Confirm the channel is in the retained dual-gated region. Repeat relevant
  isolation and channel checks for the specified relative mask shifts.
- Verify that each isolation cut reaches beyond the material it must sever.
  Check any required reference window for the intended hBN and bare substrate
  coverage and for clearance from other material and metal.
- Name the fabrication and template layers required in the delivery. Reread
  the saved mask file and repeat the relevant checks on its exported geometry.
