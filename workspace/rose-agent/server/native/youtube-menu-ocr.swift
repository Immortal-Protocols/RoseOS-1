import Foundation
import Vision
import ImageIO
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["en-US"]
request.usesLanguageCorrection = false
try VNImageRequestHandler(url: url, options: [:]).perform([request])
let rows = (request.results ?? []).compactMap { observation -> [String: Any]? in
    guard let text = observation.topCandidates(1).first else { return nil }
    let b = observation.boundingBox
    return ["text": text.string, "confidence": text.confidence, "x": b.minX, "y": 1-b.maxY, "width": b.width, "height": b.height]
}
let data = try JSONSerialization.data(withJSONObject: rows)
FileHandle.standardOutput.write(data)
