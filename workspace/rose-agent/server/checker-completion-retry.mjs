export const completionRepairInstruction = 'Your previous completion review selected complete without a nonempty verifiedAnswer. Re-evaluate the same original request and evidence. This is a format and consistency recheck, not a request to approve the task. Return complete only if the evidence supports the requested outcome, and include its verified answer. Otherwise return the appropriate non-complete status and useful next steps. Do not invent missing facts.';

const missingAnswer = result => result?.status === 'complete' &&
  (typeof result.verifiedAnswer !== 'string' || !result.verifiedAnswer.trim());

// Recheck a contradictory review once, without returning to the phone worker.
// Ordinary approval-only reviews do not require an answer and remain unchanged.
export function withCompletionRetry(review) {
  return async args => {
    const result = await review(args);
    if (!args.verifiedAnswer || !missingAnswer(result)) return result;
    args.signal?.throwIfAborted();
    const repaired = await review({...args, reviewRepair: true});
    if (missingAnswer(repaired)) {
      throw Error('The checker still returned complete without an answer after one recheck.');
    }
    return repaired;
  };
}
