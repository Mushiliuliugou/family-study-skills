import Foundation
import Vision
import CoreImage
import ImageIO
import AppKit

let context = CIContext(options: [.useSoftwareRenderer: false])
func cg(_ image: CIImage) throws -> CGImage {
    guard let result = context.createCGImage(image, from: image.extent.integral) else { throw NSError(domain:"Photo",code:1) }
    return result
}
func scan(_ image: CIImage) throws -> [VNRecognizedTextObservation] {
    let scale = min(1, 1500 / max(image.extent.width,image.extent.height))
    let small = image.transformed(by:CGAffineTransform(scaleX:scale,y:scale))
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans","en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage:try cg(small),options:[:]).perform([request])
    return request.results ?? []
}
func score(_ observations:[VNRecognizedTextObservation]) -> Double {
    observations.reduce(0) { result, o in
        guard let t=o.topCandidates(1).first else { return result }
        let count=t.string.filter { !$0.isWhitespace }.count
        var horizontal = o.boundingBox.width > o.boundingBox.height * 1.5 ? 1.0 : 0.25
        if t.string.count>=2,
           let first=try? t.boundingBox(for:t.string.startIndex..<t.string.index(after:t.string.startIndex)),
           let last=try? t.boundingBox(for:t.string.index(before:t.string.endIndex)..<t.string.endIndex) {
            let dx=last.boundingBox.midX-first.boundingBox.midX,dy=last.boundingBox.midY-first.boundingBox.midY
            if dx < 0 || abs(dy)>abs(dx) {horizontal *= 0.02}
        }
        return result + Double(t.confidence) * Double(min(count,60)) * horizontal
    }
}
func bounds(_ image:CGImage) -> CGRect? {
    let w=image.width,h=image.height;var pixels=[UInt8](repeating:255,count:w*h*4)
    return pixels.withUnsafeMutableBytes { bytes in
        guard let c=CGContext(data:bytes.baseAddress,width:w,height:h,bitsPerComponent:8,bytesPerRow:w*4,space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue) else {return nil}
        c.setFillColor(NSColor.white.cgColor);c.fill(CGRect(x:0,y:0,width:w,height:h));c.draw(image,in:CGRect(x:0,y:0,width:w,height:h))
        let p=bytes.bindMemory(to:UInt8.self);var minX=w,minY=h,maxX=0,maxY=0,total=0
        for y in 0..<h { for x in 0..<w {
            let i=(y*w+x)*4
            if min(p[i],min(p[i+1],p[i+2])) < 245 { minX=min(minX,x);maxX=max(maxX,x);minY=min(minY,y);maxY=max(maxY,y);total+=1 }
        }}
        if total < 20 || minX>=maxX || minY>=maxY {return nil}
        let margin=max(16,Int(Double(max(w,h))*0.015))
        let x=max(0,minX-margin),y=max(0,minY-margin)
        return CGRect(x:x,y:y,width:min(w,maxX+margin+1)-x,height:min(h,maxY+margin+1)-y)
    }
}

func estimateSkew(_ image: CIImage) throws -> Double {
    let scale=min(1,1100/max(image.extent.width,image.extent.height));let sample=try cg(image.transformed(by:CGAffineTransform(scaleX:scale,y:scale)))
    let w=sample.width,h=sample.height;var bytes=[UInt8](repeating:255,count:w*h*4);var points:[(Double,Double)]=[]
    bytes.withUnsafeMutableBytes { data in
        let c=CGContext(data:data.baseAddress,width:w,height:h,bitsPerComponent:8,bytesPerRow:w*4,space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue)!
        c.setFillColor(NSColor.white.cgColor);c.fill(CGRect(x:0,y:0,width:w,height:h));c.draw(sample,in:CGRect(x:0,y:0,width:w,height:h))
        let p=data.bindMemory(to:UInt8.self)
        for y in stride(from:0,to:h,by:2) {for x in stride(from:0,to:w,by:2) {let i=(y*w+x)*4;if min(p[i],min(p[i+1],p[i+2]))<150 {points.append((Double(x-w/2),Double(y-h/2)))}}}
    }
    if points.count<100 || points.count>w*h/5 {return 0}
    let offset=w+h,size=2*(w+h)+3
    func projection(_ degrees:Double)->Double {
        let a=degrees * .pi/180,co=cos(a),si=sin(a);var bins=[Int](repeating:0,count:size)
        for (x,y) in points {let row=Int((x*si+y*co).rounded())+offset;if row>=0 && row<size {bins[row]+=1}}
        return bins.reduce(0){$0+Double($1*$1)}
    }
    let baseline=projection(0);var best=baseline,angle=0.0
    for step in -28...28 {let a=Double(step)*0.25,value=projection(a);if value>best {best=value;angle=a}}
    return best>baseline*1.025 && abs(angle)>=0.5 && abs(angle)<7 ? angle : 0
}

do {
    let sourceURL=URL(fileURLWithPath:CommandLine.arguments[1]),outputURL=URL(fileURLWithPath:CommandLine.arguments[2])
    guard let source=CGImageSourceCreateWithURL(sourceURL as CFURL,nil),let props=CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],let width=props[kCGImagePropertyPixelWidth] as? Int,let height=props[kCGImagePropertyPixelHeight] as? Int,width*height<=40_000_000,let original=CIImage(contentsOf:sourceURL,options:[.applyOrientationProperty:true]) else {throw NSError(domain:"Photo",code:2)}
    let factor=min(1,3200/max(original.extent.width,original.extent.height));let base=original.transformed(by:CGAffineTransform(scaleX:factor,y:factor))
    let orientations:[CGImagePropertyOrientation] = [.up,.right,.down,.left]
    var chosen=0;var observations=try scan(base);let initial=score(observations);var best=initial
    for i in 1...3 {
        let candidate=try scan(base.oriented(orientations[i]));let value=score(candidate)
        if value > best*1.15+1 {best=value;chosen=i;observations=candidate}
    }
    var image=base.oriented(orientations[chosen]);var skew=0.0
    if observations.count>=2 {
        skew=try estimateSkew(image)
        if skew != 0 {let t=CGAffineTransform(rotationAngle:-skew * .pi/180);let extent=image.extent.applying(t).integral;image=image.clampedToExtent().transformed(by:t).cropped(to:extent)}
    }
    image=image.composited(over:CIImage(color:CIColor.white).cropped(to:image.extent.integral))
    var rendered=try cg(image);let rectangle=bounds(rendered);var cropped=false
    if let rect=rectangle,rect.width>=24,rect.height>=24,rect.width*rect.height<Double(rendered.width*rendered.height)*0.98,let cut=rendered.cropping(to:rect) {rendered=cut;cropped=true}
    let bitmap=NSBitmapImageRep(cgImage:rendered)
    guard let data=bitmap.representation(using:.jpeg,properties:[.compressionFactor:0.9]) else {throw NSError(domain:"Photo",code:3)}
    try data.write(to:outputURL)
    let result:[String:Any] = ["orientation_degrees":chosen*90,"deskew_degrees":skew,"cropped":cropped,"width":rendered.width,"height":rendered.height,"orientation_confident":best>=3,"original_width":width,"original_height":height]
    print(String(data:try JSONSerialization.data(withJSONObject:result,options:[.sortedKeys]),encoding:.utf8)!)
} catch {fputs("photo processing failed\n",stderr);exit(1)}
