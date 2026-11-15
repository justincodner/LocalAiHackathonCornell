// swift-tools-version: 6.0
// Owner: Mac owner (Justin). Beacon Helper: local speech, accessibility and HUD server.
import PackageDescription

let package = Package(
    name: "BeaconHelper",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "beacon-helper", targets: ["BeaconHelper"]),
    ],
    dependencies: [
        .package(url: "https://github.com/argmaxinc/argmax-oss-swift.git", from: "1.1.1"),
        .package(url: "https://github.com/swhitty/FlyingFox.git", from: "0.27.1"),
    ],
    targets: [
        .executableTarget(
            name: "BeaconHelper",
            dependencies: [
                .product(name: "WhisperKit", package: "argmax-oss-swift"),
                .product(name: "TTSKit", package: "argmax-oss-swift"),
                .product(name: "FlyingFox", package: "FlyingFox"),
                .product(name: "FlyingSocks", package: "FlyingFox"),
            ],
            path: "BeaconHelper",
            exclude: ["Info.plist", "BeaconHelper.entitlements"],
            resources: [.copy("HUD/Sounds"), .copy("Accessibility/ReadPage.js")],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "BeaconHelperTests",
            dependencies: ["BeaconHelper"],
            path: "Tests/BeaconHelperTests",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
