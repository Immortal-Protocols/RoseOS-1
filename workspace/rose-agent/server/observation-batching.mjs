// Keep the existing image-view event, local image files, and device safeguards.
// The trial removes an avoidable model turn; it never adds automatic UI actions.
export const OBSERVATION_BATCHING_POLICY_VERSION=1;
export function observationBatchingPolicy(enabled,controlPath){
 if(!enabled)return 'Combined screenshot observation trial is off. Earlier trial-only batching requirements are no longer active. Use the ordinary phone and image tools as needed.';
 return `Combined screenshot observation trial is ON. Keep all existing rules about WHEN a screenshot is needed, existing fast app exceptions, image quality, screen freshness, and final verification. Do not capture extra screenshots solely because this trial is enabled.
When a phone operation already requires a screenshot, capture and VIEW it in the SAME functions.exec execution. There is no need for a model turn between receiving an imagePath and opening that exact image. Use the existing exec_command and view_image tools, and forward the pixels with image(). Do not merely print image metadata.
For example, in one functions.exec execution: const r = await tools.exec_command({cmd: 'python3 ${controlPath} screenshot', max_output_tokens: 2000}); text(r.output); const shot = JSON.parse(r.output); if (shot.status === 'completed' && shot.result?.status === 'ok' && shot.result.imagePath) { const frame = await tools.view_image({path: shot.result.imagePath, detail: 'original'}); image(frame.image_url); }
Apply the same batching when an existing phone operation such as open_app returns result.visualCheck.imagePath: print the operation result, then view that returned imagePath and emit its pixels in that same execution. A visual_check_required result means the action did NOT execute. Only view its image; leave the decision about the next action to the next model turn. Do not automatically retry or issue another tap in the same execution. A failed screenshot stays a failed observation, not proof of success.
This changes delivery only: no fixed waits, reduced checks, forced screenshots on fast app commands, automatic navigation sequences, or suppressed progress.`;
}
