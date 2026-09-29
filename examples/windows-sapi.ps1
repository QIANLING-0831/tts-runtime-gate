param(
    [Parameter(Mandatory = $true)][string]$Text,
    [Parameter(Mandatory = $true)][string]$Output,
    [string]$Voice = "Microsoft Huihui Desktop",
    [int]$Rate = 0
)

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.SelectVoice($Voice)
    $synth.Rate = $Rate
    $synth.SetOutputToWaveFile($Output)
    $synth.Speak($Text)
}
finally {
    $synth.Dispose()
}
