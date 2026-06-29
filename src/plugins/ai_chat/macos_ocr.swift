import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: macos_ocr.swift IMAGE_PATH\n".utf8))
    exit(2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let image = NSImage(contentsOf: imageURL) else {
    FileHandle.standardError.write(Data("could not load image\n".utf8))
    exit(3)
}

var imageRect = NSRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &imageRect, context: nil, hints: nil) else {
    FileHandle.standardError.write(Data("could not create CGImage\n".utf8))
    exit(4)
}

let width = cgImage.width
let height = cgImage.height
let colorSpace = CGColorSpaceCreateDeviceRGB()
let bitmapInfo = CGImageAlphaInfo.premultipliedLast.rawValue
guard
    let context = CGContext(
        data: nil,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: width * 4,
        space: colorSpace,
        bitmapInfo: bitmapInfo
    )
else {
    FileHandle.standardError.write(Data("could not create RGB context\n".utf8))
    exit(4)
}
context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
guard let normalizedImage = context.makeImage() else {
    FileHandle.standardError.write(Data("could not normalize image\n".utf8))
    exit(4)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.automaticallyDetectsLanguage = true
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: normalizedImage, options: [:])
do {
    try handler.perform([request])
} catch {
    let nsError = error as NSError
    let detail = "OCR failed: \(nsError.domain) \(nsError.code) \(nsError.userInfo)\n"
    FileHandle.standardError.write(Data(detail.utf8))
    exit(5)
}

let observations = (request.results ?? []).sorted {
    let verticalDifference = $0.boundingBox.midY - $1.boundingBox.midY
    if abs(verticalDifference) > 0.02 {
        return verticalDifference > 0
    }
    return $0.boundingBox.minX < $1.boundingBox.minX
}

for observation in observations {
    if let candidate = observation.topCandidates(1).first {
        print(candidate.string)
    }
}
