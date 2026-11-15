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

final class WorktreeModelsTests: XCTestCase {
    let main = URL(fileURLWithPath: "/Users/me/Beacon")
    let worktree = URL(fileURLWithPath: "/Users/me/wt-scroll")

    func testMainCheckoutIsReadFromAWorktreesGitFile() {
        let gitFile: (URL) -> String? = { url in
            url.path == "/Users/me/wt-scroll/.git" ? "gitdir: /Users/me/Beacon/.git/worktrees/wt-scroll\n" : nil
        }
        XCTAssertEqual(Config.mainCheckout(of: worktree, readGitFile: gitFile)?.path, main.path)
        // The main checkout's .git is a folder (unreadable as a file): it is not a worktree.
        XCTAssertNil(Config.mainCheckout(of: main, readGitFile: gitFile))
        // A relative gitdir (git worktree --relative-paths) resolves against the worktree.
        let nested = URL(fileURLWithPath: "/Users/me/Beacon/.claude/worktrees/agent-1")
        XCTAssertEqual(Config.mainCheckout(of: nested, readGitFile: { _ in "gitdir: ../../../.git/worktrees/agent-1" })?.path,
                       main.path)
        XCTAssertNil(Config.mainCheckout(of: worktree, readGitFile: { _ in "gitdir: /Users/me/elsewhere/.git/modules/x" }))
    }

    func testAWorktreeWithoutTheWhisperModelUsesTheMainCheckoutsModels() {
        let onlyMain: (URL) -> Bool = { $0.path == "/Users/me/Beacon/models" }
        // The bug: started from a worktree, the Helper looked in wt-scroll/models and rejected every recording.
        XCTAssertEqual(Config.resolveModelsDir(relative: "models", root: worktree, mainCheckout: main, sttModel: "tiny.en",
                                               hasWhisperModel: onlyMain).path, "/Users/me/Beacon/models")
        // A worktree with its own model keeps it.
        XCTAssertEqual(Config.resolveModelsDir(relative: "models", root: worktree, mainCheckout: main, sttModel: "tiny.en",
                                               hasWhisperModel: { _ in true }).path, "/Users/me/wt-scroll/models")
        // Neither has it: report the worktree's own folder, as before.
        XCTAssertEqual(Config.resolveModelsDir(relative: "models", root: worktree, mainCheckout: main, sttModel: "tiny.en",
                                               hasWhisperModel: { _ in false }).path, "/Users/me/wt-scroll/models")
        // Not a worktree: unchanged.
        XCTAssertEqual(Config.resolveModelsDir(relative: "models", root: main, mainCheckout: nil, sttModel: "tiny.en",
                                               hasWhisperModel: { _ in false }).path, "/Users/me/Beacon/models")
    }

    func testRepeatingCancelsAreExplainedAndOneOffsAreNot() {
        XCTAssertNotNil(SpeechInputService.cancelMessage(reason: "stt_model_missing"))
        XCTAssertNotNil(SpeechInputService.cancelMessage(reason: "microphone_unavailable"))
        for reason in ["too_short", "empty_transcript", "shortcut", "quit", "stt_failed"] {
            XCTAssertNil(SpeechInputService.cancelMessage(reason: reason), reason)
        }
        XCTAssertNil(SpeechInputService.cancelMessage(reason: nil))
    }
}
