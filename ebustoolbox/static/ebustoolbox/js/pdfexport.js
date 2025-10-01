const jsPDF = window.jspdf && window.jspdf.jsPDF;

const pdfExportData = {
    scenarioName: "{{ scenario.name | escapejs }}",  // Escape for JS safety
    username: "{{ user.get_full_name | escapejs }}",
  };

const PDF_MARGIN = 20;
const MAP_IMAGE_WIDTH = 560;
const MAP_IMAGE_HEIGHT = 300;
const CHART_WIDTH = 400;
const CHART_HEIGHT = 250;
const LOGO_WIDTH = 70;
const LOGO_HEIGHT = 35;

// Fix missing or broken setFontType in jsPDF
if (jsPDF) {
  // Override setFontType if missing or broken
  if (!jsPDF.API.setFontType) {
    jsPDF.API.setFontType = function(fontType) {
      // Fallback: Just set the font style (normal/bold/italic)
      this.setFont(undefined, fontType);
    };
  }
} else {
  console.warn("jsPDF is not available. PDF functionality is disabled.");
}

async function prepareSvgForPdf(svgUrl, targetWidth, targetHeight) {
  // 1. Fetch and parse the SVG
  const response = await fetch(svgUrl);
  if (!response.ok) {
    throw new Error(`Failed to load SVG from ${svgUrl}`);
  }
  const svgText = await response.text();
  const parser = new DOMParser();
  const doc = parser.parseFromString(svgText, "image/svg+xml");
  const svgElement = doc.documentElement;

  // 2. Get original dimensions and ensure a viewBox is present
  let originalWidth, originalHeight;
  if (svgElement.hasAttribute('viewBox')) {
    const viewBox = svgElement.viewBox.baseVal;
    originalWidth = viewBox.width;
    originalHeight = viewBox.height;
  } else {
    originalWidth = parseFloat(svgElement.getAttribute('width')) || targetWidth;
    originalHeight = parseFloat(svgElement.getAttribute('height')) || targetHeight;
    svgElement.setAttribute('viewBox', `0 0 ${originalWidth} ${originalHeight}`);
  }

  // 3. Remove conflicting attributes and clone
  svgElement.removeAttribute('width');
  svgElement.removeAttribute('height');

  const clonedSVG = svgElement.cloneNode(true);
  clonedSVG.setAttribute('width', `${targetWidth}px`);
  clonedSVG.setAttribute('height', `${targetHeight}px`);

  // 4. Create and append to a temporary container for rendering
  const tempDiv = document.createElement('div');
  tempDiv.style.position = 'absolute';
  tempDiv.style.left = '-9999px';
  tempDiv.appendChild(clonedSVG);
  document.body.appendChild(tempDiv);

  return {
    svg: clonedSVG,
    cleanup: () => document.body.removeChild(tempDiv)
  };
}
async function addHeaderWithSvgLogo(pdf, scenarioName, username, dateStr, svgUrl) {
    const startY = 15;
    const pageWidth = pdf.internal.pageSize.getWidth();

    const { svg: preparedSvg, cleanup } = await prepareSvgForPdf(svgUrl, LOGO_WIDTH, LOGO_HEIGHT);

    try {
        await svg2pdf(preparedSvg, pdf, {
            x: PDF_MARGIN,
            y: startY,
            width: LOGO_WIDTH,
            height: LOGO_HEIGHT
        });

        pdf.setFont('helvetica', 'bold').setFontSize(16).setTextColor(40);
        pdf.text(`Szenario: ${scenarioName}`, pageWidth / 2, startY + 15, { align: 'center' });

        pdf.setFont('helvetica', 'normal').setFontSize(10).setTextColor(120);
        pdf.text(`Exportiert von: ${username} am ${dateStr}`, pageWidth - PDF_MARGIN, startY + 15 + 18, { align: 'right' });

        pdf.setDrawColor(200).setLineWidth(0.5);
        pdf.line(PDF_MARGIN, startY + LOGO_HEIGHT + 5, pageWidth - PDF_MARGIN, startY + LOGO_HEIGHT + 5);

    } finally {
        cleanup();
    }
}
function addFooter(pdf, pageNumber, totalPages) {
  const pageWidth = pdf.internal.pageSize.getWidth();
  const pageHeight = pdf.internal.pageSize.getHeight();
  pdf.setFontSize(10);
  pdf.setTextColor(150);
  pdf.line(PDF_MARGIN, pageHeight - FOOTER_LINE_OFFSET, pageWidth - PDF_MARGIN, pageHeight - FOOTER_LINE_OFFSET);
  pdf.text(`Page ${pageNumber} of ${totalPages}`, pageWidth / 2, pageHeight - FOOTER_TEXT_OFFSET, { align: 'center' });
}

async function addPageHeadersAndFooters(pdf) {
    const totalPages = pdf.internal.getNumberOfPages();
    const { scenarioName, username } = pdfExportData;
    const dateStr = new Date().toLocaleString();

    for (let i = 1; i <= totalPages; i++) {
        pdf.setPage(i);
        await addHeaderWithSvgLogo(pdf, scenarioName, username, dateStr, '/static/core/images/logo/webus_green.svg');
        addFooter(pdf, i, totalPages);
    }
}
function addNumericalDataSection(pdf, yPosition) {
  const pageWidth = pdf.internal.pageSize.getWidth();
  const sectionWidth = pageWidth - 2 * PDF_MARGIN;
  const cardX = PDF_MARGIN;
  const cardY = yPosition + 10;
  const cardPadding = 16;
  const cardCornerRadius = 8;

  pdf.setFont('helvetica', 'normal');
  pdf.setFontSize(10); // Reduced from 14
  pdf.setTextColor(30, 41, 59);
  pdf.text('INFRASTRUKTUR', PDF_MARGIN, yPosition);
  const cardHeight = 140; // Reduced height
  pdf.setDrawColor(226, 232, 240);
  pdf.setFillColor(255, 255, 255);
  pdf.setLineWidth(0.5);
  // Shadow effect
  pdf.setDrawColor(0, 0, 0, 10);
  pdf.roundedRect(cardX + 1, cardY + 1, sectionWidth, cardHeight, cardCornerRadius, cardCornerRadius, 'F');
  // Main card
  pdf.setDrawColor(203, 213, 225);
  pdf.roundedRect(cardX, cardY, sectionWidth, cardHeight, cardCornerRadius, cardCornerRadius, 'FD');

  const colWidth = (sectionWidth - 2) / 2;
  const valueBoxWidth = (colWidth - 2) / 3;

  // Data structure
  const sections = [
    {
      title: "Umläufe",
      values: [
        { value: "14768", unit: "km", label: "Längster Umlauf" },
        { value: "17106", unit: "km", label: "Kürzester Umlauf" },
        { value: "130", unit: "km", label: "Gesamtstrecke" }
      ],
      borderBottom: true
    },
    {
      title: "Szenario",
      values: [
        { value: "0 / 5", unit: "", label: "Elektrifizierte Stationen / Gesamtzahl Stationen" },
        { value: "2", unit: "", label: "Anzahl Fahrzeuge" },
        { value: "S Lichterfelde Süd, 4 mal", unit: "", label: "Häufigst angefahrene Station" }
      ],
      borderBottom: true
    },
    {
      title: "Energie und Leistung",
      values: [
        { value: "289", unit: "kWh", label: "Gesamt geladene Energie" },
        { value: "1.11", unit: "kWh/km", label: "Durchschnittl. Verbrauch" },
        { value: "0", unit: "kW", label: "Installierte Leistung" }
      ],
      borderBottom: false
    },
    {
      title: "Depots",
      values: [
        { value: "2", unit: "", label: "Anzahl Stellplätze" },
        { value: "289", unit: "kWh", label: "Energie" },
        { value: "90", unit: "kW", label: "Spitzenleistung" }
      ],
      borderBottom: false
    }
  ];

  // Scaled-down font sizes
  const titleFontSize = 9;  // Reduced from 12
  const valueFontSize = 12; // Reduced from 18
  const unitFontSize = 9;   // Reduced from 12
  const labelFontSize = 8;  // Reduced from 10

  // Draw the grid
  let currentY = cardY + cardPadding;
  const rowHeight = 60; // Reduced from 90

  // First row
  for (let col = 0; col < 2; col++) {
    const section = sections[col];
    const x = cardX + cardPadding + (col * (colWidth + 1));

    // Section title
    pdf.setFont('helvetica', 'normal');
    pdf.setFontSize(titleFontSize);
    pdf.setTextColor(100, 116, 139);
    pdf.text(section.title, x + (colWidth / 2), currentY, { align: 'center' });

    // Value boxes
    const valueY = currentY + 14; // Reduced spacing

    for (let i = 0; i < 3; i++) {
      const valueX = x + (i * (valueBoxWidth + 1));
      const valueData = section.values[i];

      // Draw divider if not last box
      if (i < 2) {
        pdf.setDrawColor(203, 213, 225);
        pdf.line(
          valueX + valueBoxWidth,
          valueY - 6,
          valueX + valueBoxWidth,
          valueY + 22
        );
      } else {
          pdf.setDrawColor(203, 213, 225);
        pdf.line(
          valueX + valueBoxWidth,
          valueY - 25,
          valueX + valueBoxWidth,
          valueY + 70
        );
      }

      // Value
      pdf.setFont('helvetica', 'bold');
      pdf.setFontSize(valueFontSize);
      pdf.setTextColor(30, 41, 59);
      const textWidth = pdf.getStringUnitWidth(valueData.value) * valueFontSize;
      pdf.text(valueData.value, valueX + (valueBoxWidth / 2) - (textWidth / 2) + 2, valueY);

      // Unit
      if (valueData.unit) {
        pdf.setFont('helvetica', 'normal');
        pdf.setFontSize(unitFontSize);
        pdf.text(valueData.unit, valueX + (valueBoxWidth / 2) + (textWidth / 2) + 2, valueY);
      }

      // Label
      pdf.setFont('helvetica', 'normal');
      pdf.setFontSize(labelFontSize);
      pdf.setTextColor(100, 116, 139);
      const labelLines = valueData.label.split('/');
      labelLines.forEach((line, idx) => {
        pdf.text(line.trim(), valueX + (valueBoxWidth / 2), valueY + 8 + (idx * 6), { align: 'center' });
      });
    }

    // Bottom border if needed
    if (section.borderBottom) {
      pdf.setDrawColor(203, 213, 225);
      pdf.line(
        x,
        currentY + 50,
        x + colWidth,
        currentY + 50
      );
    }
  }

  // Second row
  currentY += rowHeight;

  for (let col = 0; col < 2; col++) {
    const section = sections[col + 2];
    const x = cardX + cardPadding + (col * (colWidth + 1));

    // Section title
    pdf.setFont('helvetica', 'normal');
    pdf.setFontSize(titleFontSize);
    pdf.setTextColor(100, 116, 139);
    pdf.text(section.title, x + (colWidth / 2), currentY, { align: 'center' });

    // Value boxes
    const valueY = currentY + 14;

    for (let i = 0; i < 3; i++) {
      const valueX = x + (i * (valueBoxWidth + 1));
      const valueData = section.values[i];

      if (i < 2) {
        pdf.setDrawColor(203, 213, 225);
        pdf.line(
          valueX + valueBoxWidth,
          valueY - 6,
          valueX + valueBoxWidth,
          valueY + 22
        );
      }

      pdf.setFont('helvetica', 'bold');
      pdf.setFontSize(valueFontSize);
      pdf.setTextColor(30, 41, 59);
      const textWidth = pdf.getStringUnitWidth(valueData.value) * valueFontSize;
      pdf.text(valueData.value, valueX + (valueBoxWidth / 2) - (textWidth / 2) + 2, valueY);

      if (valueData.unit) {
        pdf.setFont('helvetica', 'normal');
        pdf.setFontSize(unitFontSize);
        pdf.text(valueData.unit, valueX + (valueBoxWidth / 2) + (textWidth / 2) + 2, valueY);
      }

      pdf.setFont('helvetica', 'normal');
      pdf.setFontSize(labelFontSize);
      pdf.setTextColor(100, 116, 139);
      const labelLines = valueData.label.split('/');
      labelLines.forEach((line, idx) => {
        pdf.text(line.trim(), valueX + (valueBoxWidth / 2), valueY + 8 + (idx * 6), { align: 'center' });
      });
    }
  }

  return cardY + 140 + 30;
}

document.getElementById('exportPdf').addEventListener('click', async function() {
  const btn = this;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = 'PDF wird erstellt...';

  try {
    await generatePdfReport();
  } catch (error) {
    console.error('PDF generation failed:', error);
    alert('Failed to generate PDF. Please check console for details.');
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
});


async function generatePdfReport() {
    const pdf = new jsPDF('p', 'pt', 'a4');
    let yPosition = 80;

    // 1. Add numerical data section
    yPosition = await addNumericalDataSection(pdf, yPosition) + 50;

    // 2. Add map image
    yPosition = await addMapImage(pdf, yPosition);

    // 3. Add charts
    yPosition = await addCharts(pdf, yPosition);

    // 4. Add headers and footers to all pages
    await addPageHeadersAndFooters(pdf);

    // 5. Save the final document
    pdf.save('charts-export.pdf');
}


async function addMapImage(pdf, yPosition) {
    await new Promise(resolve => map.once('idle', resolve));

    const canvas = await html2canvas(document.getElementById('map'), {
        useCORS: true,
        allowTaint: false,
        scale: 2,
        logging: true
    });

    const imgData = canvas.toDataURL({
        format: 'image/png',
        quality: 1.0,
    });

    pdf.addImage(imgData, 'image/png', PDF_MARGIN, yPosition, MAP_IMAGE_WIDTH, MAP_IMAGE_HEIGHT);

    return yPosition + MAP_IMAGE_HEIGHT + 50;
}

async function addCharts(pdf, yPosition) {
    const chartContainers = document.querySelectorAll('.chart-container');
    const pageHeight = pdf.internal.pageSize.getHeight();

    for (const container of chartContainers) {
        const chartInstance = echarts.getInstanceByDom(container);
        if (!chartInstance) continue;

        if (yPosition + CHART_HEIGHT > pageHeight - PDF_MARGIN) {
            pdf.addPage();
            yPosition = PDF_MARGIN + 20;
        }

        try {
            const svgDataURL = chartInstance.getDataURL({
                type: 'svg',
                pixelRatio: 2,
                excludeComponents: ['toolbox', 'dataZoom'],
                backgroundColor: '#FFFFFF'
            });

            const { svg: preparedSvg, cleanup } = await prepareSvgForPdf(svgDataURL, CHART_WIDTH, CHART_HEIGHT);

            await svg2pdf(preparedSvg, pdf, {
                x: PDF_MARGIN,
                y: yPosition,
                width: CHART_WIDTH,
                height: CHART_HEIGHT,
            });

            yPosition += CHART_HEIGHT + PDF_MARGIN;
            cleanup();
        } catch (error) {
            console.error(`Failed to process chart ${container.id}:`, error);
        }
    }
    return yPosition;
}
