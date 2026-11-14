import XCTest
@testable import BeaconHelper

final class JSONTests: XCTestCase {
    func testOptionalBecomesNull() throws {
        let v: String? = nil
        let s = JSON.string(["value": v as Any, "n": 1])
        XCTAssertEqual(s, #"{"n":1,"value":null}"#)
    }

    func testArgsRejectsMissingField() {
        XCTAssertThrowsError(try Args(raw: [:]).string("text"))
    }
}
