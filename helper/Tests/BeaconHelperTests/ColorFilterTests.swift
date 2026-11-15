import XCTest
@testable import BeaconHelper

/// Stands in for MediaAccessibility so tests never change the real display.
final class FakeColorFilters: ColorFilterBackend, @unchecked Sendable {
    var available = true
    var saved = ColorFilterState(enabled: false, typeValue: 1)
    var ignoresWrites = false
    var writes: [(Bool, Int?)] = []

    var isAvailable: Bool { available }
    func read() -> ColorFilterState { saved }
    func write(enabled: Bool, typeValue: Int?) {
        writes.append((enabled, typeValue))
        guard !ignoresWrites else { return }
        if let typeValue { saved.typeValue = typeValue }
        saved.enabled = enabled
    }
}

final class ColorFilterTests: XCTestCase {
    func testFilterTypeValuesMatchTheSavedPreference() {
        XCTAssertEqual(ColorFilterKind.protanopia.typeValue, 2)
        XCTAssertEqual(ColorFilterKind.deuteranopia.typeValue, 4)
        XCTAssertEqual(ColorFilterKind(typeValue: 8), .tritanopia)
        XCTAssertNil(ColorFilterKind(typeValue: 3))
    }

    func testEnablingSelectsTheFilterAndReportsThePreviousState() async throws {
        let fake = FakeColorFilters()
        let service = ColorFilterService(backend: fake, attempts: 1)
        let result = try await service.set(enabled: true, filter: "deuteranopia")
        XCTAssertEqual(result["enabled"] as? Bool, true)
        XCTAssertEqual(result["filter"] as? String, "deuteranopia")
        XCTAssertEqual(result["verified"] as? Bool, true)
        XCTAssertEqual((result["previous"] as? JSONObject)?["filter"] as? String, "grayscale")
        XCTAssertEqual(fake.saved, ColorFilterState(enabled: true, typeValue: 4))
    }

    func testDisablingWithoutAFilterKeepsTheSelectedType() async throws {
        let fake = FakeColorFilters()
        fake.saved = ColorFilterState(enabled: true, typeValue: 2)
        _ = try await ColorFilterService(backend: fake, attempts: 1).set(enabled: false, filter: nil)
        XCTAssertEqual(fake.saved, ColorFilterState(enabled: false, typeValue: 2))
        XCTAssertNil(fake.writes.first?.1)
    }

    func testAChangeThatDoesNotReadBackIsAFailure() async {
        let fake = FakeColorFilters()
        fake.ignoresWrites = true
        do {
            _ = try await ColorFilterService(backend: fake, attempts: 2, pollInterval: 0).set(enabled: true, filter: "protanopia")
            XCTFail("expected action_failed")
        } catch let error as HelperError {
            XCTAssertEqual(error.code, .actionFailed)
        } catch { XCTFail("\(error)") }
    }

    func testUnavailableFunctionsAreUnsupportedNotACrash() async {
        let fake = FakeColorFilters()
        fake.available = false
        do {
            _ = try await ColorFilterService(backend: fake).state()
            XCTFail("expected action_unsupported")
        } catch let error as HelperError {
            XCTAssertEqual(error.code, .actionUnsupported)
        } catch { XCTFail("\(error)") }
        XCTAssertTrue(fake.writes.isEmpty)
    }

    func testUnknownOrMissingFilterIsABadRequest() async {
        let service = ColorFilterService(backend: FakeColorFilters(), attempts: 1)
        for filter in ["sepia", nil] as [String?] {
            do {
                _ = try await service.set(enabled: true, filter: filter)
                XCTFail("expected bad_request for \(filter ?? "nil")")
            } catch let error as HelperError {
                XCTAssertEqual(error.code, .badRequest)
            } catch { XCTFail("\(error)") }
        }
    }
}
