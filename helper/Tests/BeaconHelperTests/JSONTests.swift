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

final class RepoRootTests: XCTestCase {
    func testRootComesFromEnvThenAncestorsThenTheRememberedLaunch() {
        let repo = URL(fileURLWithPath: "/Users/me/Beacon")
        let isRepo: (URL) -> Bool = { $0.path == repo.path }
        XCTAssertEqual(Config.resolveRoot(environment: "/x", starts: [], remembered: nil, isRepo: isRepo)?.path, "/x")
        XCTAssertEqual(Config.resolveRoot(environment: nil, starts: [URL(fileURLWithPath: "/Users/me/Beacon/build/App")],
                                          remembered: nil, isRepo: isRepo)?.path, repo.path)
        // Opened from /Applications with cwd "/": the root remembered from an earlier launch is used.
        let fromApplications = [URL(fileURLWithPath: "/"), URL(fileURLWithPath: "/Applications/Beacon Helper.app/Contents/MacOS")]
        XCTAssertEqual(Config.resolveRoot(environment: nil, starts: fromApplications, remembered: repo.path,
                                          isRepo: isRepo)?.path, repo.path)
        XCTAssertNil(Config.resolveRoot(environment: nil, starts: fromApplications, remembered: "/gone", isRepo: isRepo))
    }
}
