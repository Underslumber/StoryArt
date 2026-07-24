# Anthropometric QA for canonical adult figures

This is a production acceptance gate for StoryArt character projections, not a medical diagnosis. It applies by default to a neutral, fully visible adult standing figure with a near-orthographic camera. A direct user instruction may authorize stylized proportions; without that instruction, every hard-limit violation rejects the result.

## Required measurements

Measure the actual full-resolution output with a landmark overlay. Do not infer values from the prompt and do not write `matched`.

| Evidence field | Definition | Default accepted range |
| --- | --- | --- |
| `head_units` | crown-to-floor height divided by crown-to-chin head length | global adult acceptance `7.0-8.25`; a narrower explicit plan range, such as `7.5-8.0`, is mandatory |
| `crown_landmark` | anatomical cranial vertex, not the highest visible hair/headwear pixel | `CRANIAL_VERTEX` or `CRANIAL_VERTEX_ESTIMATED` |
| `crown_confidence` | confidence that hair volume was excluded from the crown estimate | `0.80-1.00`; lower confidence blocks head-count judgment |
| `pubic_height_fraction` | floor-to-pubic-landmark height divided by crown-to-floor height | `0.47-0.53` |
| `hip_knee` | femoral-head center to knee-joint center in one consistent image unit | positive numeric value |
| `knee_ankle` | knee-joint center to talocrural-joint center in the same unit | positive; `knee_ankle / hip_knee = 0.75-1.20` |
| `ankle_width` | narrowest ankle width divided by head length | `0.12-0.30` |
| `foot_length` | heel-to-longest-toe distance divided by head length | `0.75-1.20` |
| `view` | projection being checked | `FRONT`, `SIDE`, `BACK`, or `ASSEMBLY` |
| `foot_length_mode` | whether true heel-to-toe depth is measurable | `MEASURED`; `FORESHORTENED_DEFERRED` only for FRONT/BACK |
| `heel_endpoint`, `toe_endpoint` | endpoint visibility | both must be `VISIBLE` |
| `foot_pose` | observed foot state: `FLAT`, `TIPTOE`, `PLANTAR_FLEXED`, or `DORSIFLEXED` | required categorical evidence |
| `hip_landmark` | exact proximal landmark | must be `FEMORAL_HEAD_CENTER` |
| `knee_landmark` | exact intermediate landmark | must be `KNEE_JOINT_CENTER` |
| `ankle_landmark` | exact distal landmark | must be `TALOCRURAL_JOINT_CENTER` |
| `landmark_confidence` | confidence that all three joint centers are located correctly | `0.80-1.00`; lower confidence blocks judgment |
| `neck_head_ratio` | visible chin-to-suprasternal-notch length divided by head length | `0.20-0.48` |
| `neck_jaw_ratio` | narrowest neck width divided by jaw width | `0.50-0.90` |

The pubic landmark must remain near the vertical midpoint. If the 50% height line crosses the thigh instead, the legs are too long relative to the torso and the result fails. A 9-10-head figure, an overlong lower-leg segment, or a simultaneously long and narrow neck also fails automatically.

For `head_units`, locate the anatomical cranial vertex under the hairstyle. Long hair, lifted roots, buns, hoods, hats, horns, and ornaments are presentation pixels, not body height. If the vertex can only be estimated, label it `CRANIAL_VERTEX_ESTIMATED` and record confidence; never use the top of the hair silhouette as the crown.

`knee_ankle` always ends at the anatomical center of the ankle joint. Never extend this measurement to the heel, sole, ball of the foot, or toes. When the character stands on tiptoe or the foot is plantar-flexed, record `foot_pose=TIPTOE` or `foot_pose=PLANTAR_FLEXED`; evaluate the foot angle and heel/toe endpoints separately and do not misclassify the extended foot as extra shin length.

In a straight FRONT or BACK projection, true heel-to-toe length points mostly into image depth and must not be inferred from the vertical pixels below the ankle. Record `foot_length_mode=FORESHORTENED_DEFERRED`, keep both endpoints visible, and defer the numeric length gate to SIDE. SIDE may not defer it.

The pubic landmark is only the whole-body midpoint check. It is not the hip joint and must never be used as the start of `hip_knee`. The proximal leg landmark is the center of the femoral head, which lies above and lateral to the pubic/crotch point. If clothing, pose, projection, or hair makes that center uncertain, set confidence below `0.80`; the result remains unverified and cannot receive either a proportion `PASS` or an automatic proportion `FAIL` from the segment ratio alone.

## Acceptance procedure

1. Confirm that crown, chin, pubic landmark, hip joints, knees, ankles, heels, and toe endpoints are visible and measurable. A cropped, perspective-distorted, bent, or heavily posed image cannot establish canonical proportions.
2. Create a deterministic landmark or edge overlay against the authoritative geometry source. Label the pubic midpoint separately from the femoral-head centers; label ankle-joint centers separately from heel and toe endpoints.
3. Record every required field numerically in `--limb-qa-evidence`, including `source=`.
4. Keep anatomy, rendering style, clothing topology, safe coverage, and view correctness as independent gates. Passing one never implies another.
5. If any measured value is outside the accepted range, record the output as rejected and correct the same stage. Never promote it to SIDE, BACK, ASSEMBLY, or permanent character references.

## Local correction quality

For a point correction, never feed an already edited full frame through another full-frame generative edit. Repeated generative-over-generative passes accumulate background noise, dirty skin texture, softened linework, and identity/style drift even when the prompt says `pixel-lock`.

Return to the cleanest suitable source. Generate only a tightly cropped or masked region containing the failed anatomy, then composite only that verified region back into the clean source with a deterministic mask. Pixels outside the declared mask must remain byte-identical. If the available generator cannot enforce a local crop or mask, do not pretend a text-only `pixel-lock` is sufficient; record a blocker or use a genuinely local editing route.

Use `--user-approved-nonstandard-proportions` only when the user directly requests proportions outside these defaults. The override does not remove the measurement requirement; it records that the measured stylization was intentional.
